# cmru SPEC

CIU conventions apply: section numbers are stable identifiers (S-numbers).
Breaking changes to a section bump the wheel MAJOR and include the S-ID in the changelog.
RFC 2119 key words (MUST, SHOULD, MAY, etc.) are normative.

---

## S-CLI — CLI at a glance (the intuitive contract)

cmru is one CLI over a monorepo of independently-versioned **projects**. Everything a user
touches is named `cmru.*` so the association is unambiguous.

**Root and Git-family scope.** CMRU discovers the nearest
`cmru.orchestration.toml` as the CMRU root, even when that directory is above
several independent Git repositories. Project selection is resolved from that
root. Git snapshots, locks, branches and isolated workspaces are then selected
per project from the project's own Git family; the orchestration directory is
never treated as a Git repository merely because it contains the registry.
Each project declares `[runtime].kind = "none"` or `"ciu"`; CMRU supplies the
workspace context and does not infer or manage arbitrary Docker runtimes.

The workflow sketch below is not the complete command catalog. The canonical
registered grammar, option inventory, and semantic review are in
[S-CLI.9](#s-cli9-canonical-cli-grammar-and-semantic-audit); every CLI product update MUST keep that
inventory current in the same change.

### Verbs, in the order you use them

```
cmru status                 # 1. preview: what changed + the next version (read-only)
cmru release                # 2. isolated transaction: prepare → gate → integrate → tag → build → publish
   ├─ cmru build            #    local-only transaction: prepare → gate → build_step → retain output
   └─ cmru publish          #    run an explicit project's push step
cmru worktrees              # discover retained failed build/release worktrees (read-only)
cmru tool-deps [--allow-stale-tool-deps] [--refresh PROJECT]
                             # verify declared tool dependencies (S15): integrity/authenticity/freshness
                             # (network; also runs inside `release`'s preflight — never during tests)
cmru versions init [all|P[,P...]] [--dry-run]    # derive source targets from package manifests
cmru versions resolve [all|P[,P...]] [--dry-run] # explicitly resolve and write native artifacts
cmru versions check [all|P[,P...]] [--json]      # read-only fresh registry comparison
cmru changelog P --backfill-tag TAG  # migration: catalog an already-published tagged release
cmru cleanup --remove-assets 30d --dry-run
                                  # preview the configured age-based remote cleanup
cmru cleanup --remove-assets 30d --yes
                                  # 3. prune old releases/images after confirmation
cmru abandon [BRANCH] [--dry-run] [--yes] [--config PATH]
                                  # inspect and discard retained local release transactions
cmru cleanup P --delete-unmanaged-release-tag TAG --yes
                                  # delete one old GitHub Release only, never its Git tag
cmru cleanup P --delete-build-output ID --yes
                                  # delete one exact local non-release output record
cmru abandon PATH --yes           # discard one exact inspected failed build worktree (absolute path)
cmru version                      # print the CMRU version

cmru resolve P    # consumer: highest-semver published version  (read-only)
cmru get-py P    # consumer: emit a standalone installer
cmru run     [--step NAME ...] [--dry-run]
                                  # named steps (repeatable), or configured default_steps when omitted
                                  # --dry-run previews resolved projects/commands; nothing runs
```

**S-CLI.1** `release` is the normal path. A failed transaction retains its worktree for
inspection. Before tagging, an operator MAY commit corrections on that retained release
branch and resume that exact worktree; prepare and the required gate run again against the
corrected branch tip, and that commit is the candidate CMRU tags, builds, publishes, and
promotes. Uncommitted changes MUST be refused on resume so the released candidate cannot
silently omit them. Resume MUST use the candidate's recorded project scope when the target is
omitted; a supplied target MUST match that scope exactly. A retained candidate with no scope
record requires an explicit target. `cmru worktrees` MUST display the recorded scope and a
scope-safe resume command only when the metadata is readable, and MUST state that an external
config from the original release must be passed again. `--resume <worktree>` is not a
durable post-tag publication retry. CMRU
MUST NOT silently reuse, move, or republish an existing tag. Durable post-tag publication
recovery is not implemented; see KI-06.

**S-CLI.2** `status` and `release` MUST operate only on the orchestrated set
(`orchestration.project_order`); a project is released only once it is listed there.

**S-CLI.3** Verbs that write to the host, source tree, or remote service
MUST be clearly distinguished in `--help` from read-only verbs. `--dry-run` MUST be exposed
on mutating verbs and MUST preview the complete selected action without performing it. A
verb that is read-only MUST NOT expose `--dry-run` as a no-op. Conditional writers such as
`dependencies --write`, `standards --update`, and `tool-deps --refresh` require their
explicit mutation selector when `--dry-run` is supplied.

**S-CLI.6 — Version refresh is explicit.** `cmru versions init` and `cmru versions resolve`
are the only CMRU commands that create or refresh version targets and their native outputs.
`cmru build`, `cmru release`, tester gates, and schedules MUST NOT invoke them implicitly.
`cmru versions check` performs fresh registry reads and MUST NOT write files.

**S-CLI.7 — The installed CLI grammar has one source.** The `cmru` entrypoint
MUST declare its public verbs, arguments, aliases, option
constraints, help text, and handlers through `cli-extended` registries. CMRU MUST NOT
maintain a second hand-written parser or a hand-written `usage()` synopsis for that
entrypoint. Root help is the registered verb catalog; `cmru help VERB` and `cmru VERB
--help` render each command's declared grammar. Nested commands MUST delegate their
remaining argv to the registered child CLI rather than parse it a second time. An accepted
option MUST affect the behavior named by its help, and unsupported options MUST fail with
status 2. The long `--help` spelling is the shared interface; there is no `-h` alias.
Invoking a registered command group without a child verb prints that group's registered
catalog and exits successfully; unknown verbs and malformed arguments fail with status 2.
`cmru main(argv)` MUST return the dispatched status for embedding, with console scripts
propagating it as the process exit status.

**S-CLI.8 — Release abandonment is exact and dry-run safe (KI-29).**
`cmru abandon [BRANCH] [--config PATH]` MUST inspect retained release transactions only.
`--config` MUST select the same project policy used to validate the recorded scope; when
omitted, CMRU uses normal config discovery. This is required for transactions whose
multi-project scope came from an external orchestration file. With no branch,
it MUST display the complete retained release-candidate set; with a branch, it MUST match
one exact managed release branch and MUST NOT widen or prefix-match the selection. Before
confirmation it MUST show the branch, worktree, recorded project scope, and every known
origin candidate or release coordinate. An interactive confirmation is required unless
`--yes` is supplied; `--yes` applies to the complete displayed set. Declining MUST make no
changes.

Abandonment MUST refuse a candidate whose results or remote refs show publication or
promotion, or whose worktree, scope, progress, backup-ref, or project publication policy is
missing, stale, malformed, or ambiguous. Remote inspection failures MUST fail closed.
After a successful `ls-remote --heads` response, absence of `refs/heads/main` or a malformed
object ID for any requested ref is indeterminate promotion state and MUST withhold abandonment
just like a failed remote query.
Every new release transaction MUST capture the exact origin tag refs before release work starts.
Abandonment MUST compare reachable remote tags with that immutable snapshot, including tags at
the original snapshot commit, and MUST withhold cleanup when a reachable tag is new, retargeted,
or cannot be classified because the retained transaction predates snapshots. After confirmation,
CMRU MUST recheck the exact candidate and main branch refs and the complete tag-ref set before
the first mutation. Candidate deletion MUST use a lease against the inspected object ID; local
cleanup MUST wait until remote deletion has been verified.
Abandonment MUST remove only the selected CMRU origin candidate ref, local worktree and
branch, and transaction sidecars. It MUST NOT rewrite `origin/main`, delete public release
assets, or remove unrelated refs. If deleting the remote candidate fails or cannot be
verified, the local worktree and sidecars MUST remain for inspection.

When no local worktree matches an exact `cmru-release-*` BRANCH and that branch exists on
origin (KI-35), abandonment MUST retire it only if every commit on it is already on
`origin/main`, using the same lease against the inspected object ID, a read-back, and removal
of that transaction's sidecars; a candidate with commits `origin/main` lacks MUST be withheld
(exit 4), and an unreachable origin or missing objects MUST fail closed (exit 2).

`--dry-run` MUST be strictly read-only: it may inspect Git/sidecar state and render the
complete candidate plan, but MUST NOT call a mutation helper, remove local or remote refs,
remove a worktree, delete a sidecar, or alter public assets. Candidate output MUST state
that the worktree and its in-worktree logs/artifacts will be removed and that `origin/main`
will remain unchanged.

`cmru cleanup` is separate from abandonment. Its configured remote policy may delete GitHub
Release records and their assets, matching Git tags, and GHCR package versions; it MUST NOT
be described as deleting a retained local release transaction or its candidate branch.
After a cleanup plan is confirmed, each GitHub Release deletion MUST re-fetch the captured
Release ID and verify its tag and current selection policy still match the preview. A missing,
ambiguous, retagged, asset-changed, or newly ineligible Release MUST be skipped for a later
preview; its matching Git tag MUST also be kept. A planned `steps.clean` MUST receive the
highest-semver Release remaining after the confirmed actions, not the preview's hypothetical
deletion set.

**S-CLI.4 — Retained-worktree discovery.** `cmru worktrees` is read-only and derives the
current Git repository without loading a CMRU config. It MUST list every CMRU-managed
`cmru-release-*` and `cmru-build-*` worktree (and legacy nested `cmru/release/*`,
`cmru/build/*` ones; see S-CLI.5b), including a path not visible through the current
bind-mount view. The adapter MUST use `worktree.list_git_worktrees()` and MUST NOT stat a
literal path that may name another filesystem namespace. It MUST preserve Git's reported HEAD
whether or not the record is prunable. JSON MUST report that HEAD as `source_commit` and the
native marker as boolean `prunable`; it MUST NOT claim that the marker proves path visibility
or absence. The adapter MUST print an exact resume command only when the shared inventory does
not mark the path prunable and the recorded release scope is readable; the command MUST include
that exact scope. Scope metadata MUST be read under the shared Git directory using the inventory
branch fact; the adapter MUST NOT stat `workspace.path` to discover or read scope. Each JSON
release record MUST include `project_scope_state` with exactly one of `recorded`, `missing`, or
`unreadable`, and `project_scope`, which MUST contain the exact project-name array only for
`recorded` and MUST be null otherwise. Build records MAY omit both fields. With missing scope
metadata it MUST require an explicit target, and with malformed or unreadable metadata it MUST
withhold the command. If the transaction does not record the external config path, the output
MUST tell the operator to repeat that path. Build discard commands follow the same prunable
rule. These are action policies, not claims that a path is accessible from this process. The
adapter MUST never guess a cleanup target.

For shared records created by CMRU, the record purpose (`cmru-release` or `cmru-build`) MUST
agree with the transaction branch before CMRU offers or performs a transaction action. The
`cmru-legacy` purpose is accepted only with a matching release/build branch; recordless legacy
worktrees MAY be listed for inspection but MUST NOT start a transaction child. Before resuming a
recordless legacy release, CMRU MUST verify the registered linked-worktree path, exact source
Git family, release branch, and valid release-progress commit as an ancestor of the candidate,
then adopt it through the shared
worktree lifecycle API. Every child MUST match its ownership record's workspace ID, worktree
path, branch, source root, and base commit to the supplied routing context. Non-prunable
inventory is not a filesystem-access guarantee; every operation MUST validate the exact
checkout through shared lifecycle preflight.

**S-CLI.5 — Isolated release transaction.** `release` MUST NOT publish from the caller's
working tree. For each selected project it resolves that project's Git family and acquires
its exclusive lock; the CMRU orchestration root itself is not used as a Git root. It rejects
local-only commits on local `main` that the remote snapshot would omit (and warns if local
`main` is behind), and
rejects any uncommitted change (tracked or untracked) under a project's own path for every
project in this run's scope (`<name>`, else every orchestrated project — the same
`project_order`-derived set `release` itself iterates; the deprecated, ignored
`orchestration.default_projects` plays no part) — skipped entirely for `--dry-run` (nothing is published, so
there is nothing to protect). `--allow-uncommitted` overrides this second check only; there is
no override for local-only commits. It then fetches `origin/main`, creates an ephemeral
`cmru-release-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>` worktree (KI-16; see S-CLI.5b) at that exact
remote commit, and re-execs there. All caller
working-tree edits that survive the preflight (i.e. that don't touch a released project's path)
are still ignored: they cannot enter the immutable remote snapshot regardless.
The child MUST resolve each selected project's `cmru.toml` from the isolated
snapshot. If the loaded orchestration paths already point inside that snapshot,
it MUST use them relative to the snapshot root directly; if the central
orchestration file is external, it MUST map project paths relative to the
source Git root. It MUST reject project paths outside both roots and any
resolved project config that escapes the isolated snapshot.
Before taking the in-place transaction-child path, CMRU MUST verify the shared ownership record
and exact workspace ID, path, source root, branch, and base commit against the supplied routing
context. Incomplete context or a recordless secondary checkout MUST fail closed; branch names
and environment variables alone MUST NOT authorize publishing or promotion.

#### S-CLI.5a — Projects release one after another, not in a shared batch

Inside each
Git-family workspace, every changed project in that family (`orchestration.project_order`,
filtered to what actually changed) runs its own full cycle — prepare → gate → tag → build →
publish → promote — to completion before the next project's cycle begins. Independent Git
families are dispatched as separate ordered transactions; their results are coordinated but
cannot be one atomic Git commit. The gate runs in the real gate environment before any tag or
public artifact. The public artifact is built from the exact gated candidate commit; only after
that build/publish succeeds does cmru push that same `HEAD` to `origin/main`.
A non-fast-forward rejection (REL-04) never rewrites the candidate by rebasing it onto a
different source commit and never force-pushes: cmru fetches, verifies that the new `origin/main`
commits did not touch the released project's own paths, merges `origin/main` INTO the candidate
(`--no-ff`, `Merge origin/main into release candidate <tag>`) and pushes again, at most three
attempts. A conflict (aborted, candidate unchanged), a touched project path, unknown paths or
exhausted attempts fail closed after publication with manual recovery instructions; the tag and
published state are kept. Non-race push failures (authentication, hooks, network) fail
immediately. A failed build (never a failed publish) after the tag push rolls that tag back,
locally and on `origin`, each deletion pinned to the object cmru pushed, and records an
absence proof so `--resume` proceeds; once the publish (`push`) step has begun the tag is never
touched and the message prints the tag, its object id and the exact recovery commands.
The release-inputs commit carries a `Cmru-Release-Candidate: <tag>` trailer (REL-08); a
candidate branch MUST NOT be merged into `main` by hand.
If a versioning strategy creates a mechanical version commit after the initial pre-tag gate,
cmru runs the gate again on that exact candidate before it publishes.

The origin candidate branch is durable. cmru pushes it once up front, refreshes it after every
prepare or tag commit, and refreshes it before each public build or publish step. A crashed
machine or lost worktree leaves the exact candidate on that branch for inspection. The branch
is removed only after the complete transaction succeeds; on failure it stays with the retained
worktree. This ordering lets a later project (e.g. an OCI image) resolve an earlier project's
(e.g. a wheel) brand-new release within the same `cmru release` run, instead of always trailing
one run behind.

On success: the origin candidate branch and the local worktree/branch are removed, and cmru
attempts to sync the caller's local `main` with `origin/main`: a fast-forward when local main
hasn't moved (the common case), or a `git rebase` when it has (e.g. ongoing work in another
terminal while the release built) — rebase, not merge, for the caller's local main. The only
merge commit the pipeline ever produces is the REL-04 `Merge origin/main into release
candidate` commit made inside the isolated candidate when main advanced during the gate.
Safe to replay because a release only ever commits declared,
mechanical generated paths (S-REL.4a), never hand-edited source, so local commits essentially
never touch the same files. When the caller is currently on `main`, cmru first requires the
checkout to be clean, including tracked and untracked changes. A dirty checkout returns a
false sync result without invoking `git rebase` or `git rebase --abort`, leaves both the dirty
files and local `main` ref untouched, and reports that exact reason with the remedy to commit
or stash all the changes (including ignored content, for example with `git stash -a`) before
running `git rebase origin/main`. A clean checkout's genuine content conflict invokes
`git rebase --abort` and is reported as a conflict. A different clean-rebase failure (such as
a hook, an already-active rebase, or another Git/host failure) is reported as undetermined,
not as a conflict; any active rebase state is aborted only when Git shows that state exists.
A false cleanup result does not change the primary release outcome, but it MUST be reported
rather than ignored.

```
Before the release:
  origin/main:     ──●(base)
  local main:      ──●(base)                          [repo_root's own checkout — untouched]
  candidate branch: (does not exist yet)

The release runs entirely inside an ISOLATED WORKTREE. The candidate branch is pushed to origin
before any project starts and refreshed as each candidate commit is made:

  candidate:       ──●(base)──●(A1: alpha's prep/tag commit, if any)
                                  │
                                  ├─ gate → build → publish alpha
                                  └─ promote exact A1 → origin/main

  origin/main:     ──●(base)──●(A1)

  candidate:       ──●(base)──●(A1)──●(B1: beta's prep/tag commit, if any)
                                             │
                                             ├─ gate → build → publish beta
                                             └─ promote exact B1 → origin/main

  origin/main:     ──●(base)──●(A1)──●(B1)
  local main:      ──●(base)                 ← still here until final cleanup

Meanwhile, if the caller committed their own work locally while the release built:
  local main:      ──●(base)──●(D1)──●(D2)             [unrelated local work]

When the caller checkout is clean, `sync_local_main` rebases local main onto the new
origin/main tip, once, after every project in this run has finished:
  local main:      ──●(base)──●(A1)──●(B1)──●(D1')──●(D2')    ← D1/D2 replayed (new hashes), linear

When the caller is dirty, cleanup refuses before the rebase instead:
  local main:      ──●(base)──●(D1)──●(D2)             [dirty files also remain in place]
  origin/main:     ──●(base)──●(A1)──●(B1)
  result:          warning with the dirty-checkout reason; local main/ref untouched
```

Deleting the release branch on success (both locally and its origin candidate branch) is cleanup of a
now-redundant ref — A1/B1 are already permanently part of `origin/main`'s history, so the
branch's job is done. That deletion does nothing to `local main` by itself; `sync_local_main`
is the only step that touches it. If synchronization returns false, the parent reports
whether the caller was dirty, a clean rebase conflicted, or a non-current diverged local
`main` was deliberately not force-moved; it never presents a dirty checkout or another
undetermined clean-rebase failure as a rebase conflict, or claims that local main was
synchronized. The warning is the reason captured by that synchronization call, not a later
guess based only on the checkout's final state.

On failure: the local worktree/branch and its origin candidate branch are retained for
inspection — `release` never resumes one automatically; the caller explicitly chooses
`--resume <path>` to continue that exact attempt, or lets the next `release` invocation start
fresh instead. Promotion is the final step of a project's cycle, so `origin/main` contains only
earlier, fully completed projects. cmru does not create a source-tree revert commit and does not
silently rebase a candidate after its artifact was built. If publication succeeded but promotion
could not be completed (REL-04 stop conditions), the candidate SHA and public artifact remain visible together on the
retained branch/logs; resolving that post-publication state is an explicit operator action.
Local `main` cleanup is attempted with the same clean-checkout guard regardless of outcome; a
false result is reported and does not claim that local main was synchronized. On a later fresh
release, tag-based plan detection skips projects already released; `--resume` remains an
explicit continuation of the retained candidate and is bound to its recorded project scope.

Explicit local abandonment is the separate top-level `cmru abandon [BRANCH]` lifecycle
operation (S-CLI.8). `cmru release` never abandons a failed worktree implicitly.

The repository-root secret document is copied mode `0600`, never committed.

**S-CLI.5b — Transaction branch/worktree naming (KI-16, shared-library aligned).** Every
`cmru-release-*` and `cmru-build-*` transaction this tool creates is named:

```
branch:     cmru-<purpose>-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>
directory:  .worktrees/cmru-<purpose>-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>
```

The branch is a FLAT single token with no nested ref path, so the branch string and the
worktree directory basename are byte-for-byte identical — true 1:1 naming, matching ciu's
`<prefix>-<YYYYMMDD_HHMMSS>-<feature>` scheme. `<purpose>` is `release` or `build`.
`<YYYYMMDD_HHMMSS>` is UTC, for chronological sort, with `_` separating date from time so that
boundary stays visually distinct from the `-` field separators. `<scope>` is the the selected target
value when the run is scoped, sanitised to `[a-z0-9-]`, else `all`. The shared allocator derives
a six-character lower-case base-36 identity and exposes it in the branch/worktree token,
family record, and child environment; it does not append an unrelated UUID. CMRU supplies the
allocator's explicit canonical allocation identity path while constructing that visible token,
because hashing a final basename that already contains its own digest would be circular. The
record persists and rechecks that identity input. If the identity is already claimed by a
different path, allocation refuses and names both paths. The directory basename equals the
branch; the allocator refuses any existing path, empty or not, before calling `git worktree add`.

Discovery, resume, and cleanup recognise a transaction branch through predicates
(`_is_release_branch`, `_is_build_branch`) that accept BOTH the flat `cmru-<purpose>-` names
created here AND the legacy nested `cmru/<purpose>/` prefix, so a worktree retained under either
the current scheme or the OLDER `cmru/<purpose>/<12-hex>` naming remains just as discoverable
(`cmru worktrees`, `list_cmru_workspaces`), resumable (`--resume`), and removable — nothing in
discovery or cleanup parses the directory name. The shared `worktree.list_git_worktrees()` API
owns the NUL-safe `git worktree list --porcelain -z` parser; CMRU filters its typed branch and
HEAD facts by transaction policy and preserves Git's `prunable` state without probing a path
that may belong to another filesystem namespace. A shared CMRU record, when present, supplies
the lifecycle context; the legacy removal bridge is used only when no shared record exists.

### File conventions (all `cmru.`-prefixed)

| File | Tracked? | Purpose |
|---|---|---|
| `<project>/cmru.toml` | committed | Complete portable product contract. **No secrets.** |
| `cmru.orchestration.toml` | committed | Nearest CMRU root: central facts, ordering/dependencies/cleanup. |
| `cmru.secret.toml` | gitignored | Repository credential document; optional explicit per-project overrides (see S2.4). |
| `cmru.project.sample.toml` | committed | Template for a project contract (no secrets). |
| `cmru.vars` | gitignored | Generated `KEY=VALUE` build vars a step emits for later steps. |
| `cmru` console script | installed | Canonical portable entry point for every verb. |
| `cmru/build-initial-standalone.sh` | committed | Fresh-checkout bootstrap that builds the first CMRU wheel without CMRU installed. |

**S-CLI.11 — Retired names.** The names `release.toml`, `release.sample.toml`, `.release-vars`,
`build-push.toml`, `release-all.py`, `release-runner.py` are **retired and removed** — no
legacy remains. The installed `cmru` console script is the only general release entry
point; native `cmru release` owns the aggregate log and live tee.

---

### S-CLI.9: Canonical CLI grammar and semantic audit

The tables in this clause are the canonical current-state record for CMRU's
installed `cmru` command surface and the
one supported executable module adapter, `python -m cmru.handlers`. They record registered
leaf verbs and each exact positional/option spelling, including hidden
internal options and deprecated aliases. Shared options are recorded once per
entrypoint family; verb-local options are listed per leaf. An option's absence
from a leaf is deliberate.

**Keeping the contract current is mandatory.** Any product change that adds,
removes, renames, aliases, re-scopes, constrains, or changes the behavior of a
verb, positional argument, option, default, or choice MUST run
`cli-extended surface sync`, review the new or changed candidates in
`docs/cli-review.toml` (and `docs/cli-review-findings.toml`), and update the
contract prose below in the same change. The generated region, the manifest
`docs/cli-surface.json` and every reviewed case are checked against the built
registry (including aliases, hidden flags, constraints and help groups) by
`cli-extended surface check` and by `tests/test_cli_spec_inventory.py`; each
active case is linked to a probe in `tests/test_cli_review_cases.py` by the
cli-extended pytest plugin. The catalog is a GRAMMAR and EARLY-REFUSAL contract:
its probes run without a project config, so they pin spellings, choices,
constraints, help groups and the refusals made before config discovery.
Behaviour past config discovery (option effects, output shape, dry-run,
confirmation, exit status) is pinned by each verb's own tests, not by the
catalog. A grammar change without those updates is incomplete.

`cli-extended` supplies the common presentation options shown here and the
built-in `help [VERB]`, `version`, `--help`, and `--version` interfaces. Its
long help spelling is intentional; there is no `-h` alias. The installed CMRU
CLIs and the active handlers module adapter render these common controls from
their registries. Long options require their exact spelling: registry parsers
disable argparse prefix abbreviation, so a removed option cannot be accepted
accidentally as a prefix of another option.
CMRU's generated standalone `get.py` remains an intentional exception: it is a
separate installer artifact with its own `argparse` parser; `cmru get-py`
itself uses the registered CMRU grammar below.

#### Grammar inventory

The region below is generated from the registered `cmru` registry by
`cli-extended surface sync`; never edit it by hand. There is ONE configured CLI
(`id = "cmru"`): the root registry mounts the handlers registry as `cmru handler`,
and the bootstrap-only `python -m cmru.handlers` builds that same registry under
the same identity, so its leaves are the `cmru handler ...` rows (a test asserts the
two builders are one). `cli-extended surface check` fails when the region, the
committed `docs/cli-surface.json`, or the reviewed catalog `docs/cli-review.toml`
is stale.

<!-- cli-extended-surface:start -->
## Generated CLI surface and semantic review

Executable: `cmru` via `cmru`; built-ins: `help`, `help <verb>`, `version`, `--help`, `--version`. Empty argv: shows help.

Surface schema: `7`; review catalog schema: `1`; library contract: `cli-extended` v1.

The manifest records registered syntax. The review catalog owns expected behavior, effects, rationale, and test references.

### Command routes

| Surface ID | Route kind | Invocation path | Invocation mode | Nested commands | Help summary | Description | Group | Behavior | Confirmation | Synopsis/usage overrides | Delegated metadata | Parser settings | Parser callback | Opaque fields | Parser completeness |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| route:entrypoint:cmru/abandon | invocation | abandon | command route; parser handles remaining tokens |  | Inspect and discard retained local release transactions (no argument) or one retained build or release worktree (BRANCH or PATH). Removes the exact CMRU backup branch, worktree, in-worktree logs/artifacts, and transaction sidecars; it refuses release transactions with publication or promotion evidence. A cmru-release-* BRANCH that exists only on origin (no local worktree) is retired only when every commit is already on origin/main; otherwise it is withheld (exit 4). [mutating; dry-run] | Inspect and discard retained local release transactions (no argument) or one retained build or release worktree (BRANCH or PATH). Removes the exact CMRU backup branch, worktree, in-worktree logs/artifacts, and transaction sidecars; it refuses release transactions with publication or promotion evidence. A cmru-release-* BRANCH that exists only on origin (no local worktree) is retired only when every commit is already on origin/main; otherwise it is withheld (exit 4). | MAINTENANCE | mutating, dry-run | true | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; abandon: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/abandon/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/build | invocation | build | command route; parser handles remaining tokens |  | Run the isolated build step. [mutating; dry-run] | Run the isolated build step. | MODIFICATION | mutating, dry-run | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; build: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/build/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/changelog | invocation | changelog | command route; parser handles remaining tokens |  | Backfill history for an already-published tagged release. [mutating; dry-run] | Backfill history for an already-published tagged release. | MODIFICATION | mutating, dry-run | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; changelog: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/changelog/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/cleanup | invocation | cleanup | command route; parser handles remaining tokens |  | Remove remote release assets (--policy, --remove-assets, --delete-unmanaged-release-tag) or one retained local build record (--delete-build-output); one mode is required. It does not abandon release transactions. [mutating; dry-run] | Remove remote release assets (--policy, --remove-assets, --delete-unmanaged-release-tag) or one retained local build record (--delete-build-output); one mode is required. It does not abandon release transactions. | MAINTENANCE | mutating, dry-run | true | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; cleanup: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/cleanup/--delete-build-output.type", "option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag.type", "option:route:entrypoint:cmru/cleanup/--log-prefix-time-short.action", "option:route:entrypoint:cmru/cleanup/--remove-assets.type"] | complete |
| route:entrypoint:cmru/dependencies | invocation | dependencies | command route; parser handles remaining tokens |  | Show and preflight the project dependency graph; --write updates its generated block (read-only without it). [mutating; dry-run] | Show and preflight the project dependency graph; --write updates its generated block (read-only without it). | MIXED OPERATIONS | mutating, dry-run | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; dependencies: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/dependencies/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/doctor | invocation | doctor | command route; parser handles remaining tokens |  | check this tool's environment and report problems | check this tool's environment and report problems | MAINTENANCE |  | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; doctor: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/doctor/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/get-py | invocation | get-py | single-command; empty remainder is parsed |  | Emit the standalone Python installer. [mutating] | Emit the standalone Python installer. | MODIFICATION | mutating | true | {} | [{"behavior": ["mutating", "dry-run"], "confirmation": false, "description": "Render the configured standalone installer for one or more projects. Without --output/--output-dir it prints one project's installer to stdout and writes nothing.", "group": "MODIFICATION", "id": null, "name": "get-py", "summary": "Render the configured standalone installer for one or more projects. Without --output/--output-dir it prints one project's installer to stdout and writes nothing. [mutating; dry-run]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; get-py: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/get-py/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/handler | delegate-group | handler | delegated group; child CLI parses remaining tokens | delegated: route:entrypoint:cmru/handler/wheel-build, route:entrypoint:cmru/handler/wheel-publish, route:entrypoint:cmru/handler/wheel-validate, route:entrypoint:cmru/handler/tarball-publish, route:entrypoint:cmru/handler/bundle-manifest, route:entrypoint:cmru/handler/tarball-validate, route:entrypoint:cmru/handler/oci-image-build, route:entrypoint:cmru/handler/oci-image-push | Run a concrete project-step handler. [mutating] | Run a concrete project-step handler. | MODIFICATION | mutating | true | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | [] | complete |
| route:entrypoint:cmru/handler/bundle-manifest | invocation | handler bundle-manifest | command route; parser handles remaining tokens |  | Write the installer manifest (files + sha256) into a staged bundle dir. [mutating; dry-run] | Write the installer manifest (files + sha256) into a staged bundle dir. | MODIFICATION | mutating, dry-run | false | {} | [{"behavior": ["mutating"], "confirmation": true, "description": "Run a concrete project-step handler.", "group": "MODIFICATION", "id": null, "name": "handler", "summary": "Run a concrete project-step handler. [mutating]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler bundle-manifest: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/handler/bundle-manifest/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/handler/oci-image-build | invocation | handler oci-image-build | command route; parser handles remaining tokens |  | Build an OCI image with docker buildx bake. [mutating; dry-run] | Build an OCI image with docker buildx bake. | MODIFICATION | mutating, dry-run | false | {} | [{"behavior": ["mutating"], "confirmation": true, "description": "Run a concrete project-step handler.", "group": "MODIFICATION", "id": null, "name": "handler", "summary": "Run a concrete project-step handler. [mutating]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler oci-image-build: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/handler/oci-image-build/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/handler/oci-image-push | invocation | handler oci-image-push | command route; parser handles remaining tokens |  | Push an OCI image to its registry. [mutating; dry-run] | Push an OCI image to its registry. | MODIFICATION | mutating, dry-run | false | {} | [{"behavior": ["mutating"], "confirmation": true, "description": "Run a concrete project-step handler.", "group": "MODIFICATION", "id": null, "name": "handler", "summary": "Run a concrete project-step handler. [mutating]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler oci-image-push: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/handler/oci-image-push/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/handler/tarball-publish | invocation | handler tarball-publish | command route; parser handles remaining tokens |  | Publish the built tarball to GitHub Releases. [mutating; dry-run] | Publish the built tarball to GitHub Releases. | MODIFICATION | mutating, dry-run | false | {} | [{"behavior": ["mutating"], "confirmation": true, "description": "Run a concrete project-step handler.", "group": "MODIFICATION", "id": null, "name": "handler", "summary": "Run a concrete project-step handler. [mutating]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler tarball-publish: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/handler/tarball-publish/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/handler/tarball-validate | invocation | handler tarball-validate | command route; parser handles remaining tokens |  | Validate the resolved latest tarball release. | Validate the resolved latest tarball release. | EXPLORATION |  | false | {} | [{"behavior": ["mutating"], "confirmation": true, "description": "Run a concrete project-step handler.", "group": "MODIFICATION", "id": null, "name": "handler", "summary": "Run a concrete project-step handler. [mutating]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler tarball-validate: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/handler/tarball-validate/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/handler/wheel-build | invocation | handler wheel-build | command route; parser handles remaining tokens |  | Build the project's wheel into dist/. [mutating; dry-run] | Build the project's wheel into dist/. | MODIFICATION | mutating, dry-run | false | {} | [{"behavior": ["mutating"], "confirmation": true, "description": "Run a concrete project-step handler.", "group": "MODIFICATION", "id": null, "name": "handler", "summary": "Run a concrete project-step handler. [mutating]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler wheel-build: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/handler/wheel-build/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/handler/wheel-publish | invocation | handler wheel-publish | command route; parser handles remaining tokens |  | Publish the built wheel to GitHub Releases. [mutating; dry-run] | Publish the built wheel to GitHub Releases. | MODIFICATION | mutating, dry-run | false | {} | [{"behavior": ["mutating"], "confirmation": true, "description": "Run a concrete project-step handler.", "group": "MODIFICATION", "id": null, "name": "handler", "summary": "Run a concrete project-step handler. [mutating]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler wheel-publish: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/handler/wheel-publish/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/handler/wheel-validate | invocation | handler wheel-validate | command route; parser handles remaining tokens |  | Validate the resolved latest wheel release. | Validate the resolved latest wheel release. | EXPLORATION |  | false | {} | [{"behavior": ["mutating"], "confirmation": true, "description": "Run a concrete project-step handler.", "group": "MODIFICATION", "id": null, "name": "handler", "summary": "Run a concrete project-step handler. [mutating]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; handler wheel-validate: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/handler/wheel-validate/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/init | invocation | init | single-command; empty remainder is parsed |  | Adopt a project with the guided scaffolding wizard. [mutating] | Adopt a project with the guided scaffolding wizard. | MODIFICATION | mutating | true | {} | [{"behavior": ["mutating", "dry-run", "interactive"], "confirmation": true, "description": "Adopt a folder by creating a validated project or monorepo contract.", "group": "MODIFICATION", "id": null, "name": "init", "summary": "Adopt a folder by creating a validated project or monorepo contract. [mutating; dry-run; interactive]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; init: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/init/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/publish | invocation | publish | command route; parser handles remaining tokens |  | Run the project publish step from a verified retained build (--build-output) or, explicitly, from the caller's checkout (--from-checkout). [mutating; dry-run] | Run the project publish step from a verified retained build (--build-output) or, explicitly, from the caller's checkout (--from-checkout). | MODIFICATION | mutating, dry-run | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; publish: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/publish/--build-output.type", "option:route:entrypoint:cmru/publish/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/release | invocation | release | command route; parser handles remaining tokens |  | Release selected projects from an isolated origin/main snapshot. [mutating; dry-run] | Release selected projects from an isolated origin/main snapshot. | MIXED OPERATIONS | mutating, dry-run | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; release: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/release/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/resolve | invocation | resolve | single-command; empty remainder is parsed |  | Resolve a project's latest published artifact. | Resolve a project's latest published artifact. | EXPLORATION |  | false | {} | [{"behavior": [], "confirmation": false, "description": "Resolve release version, tag, asset URL and digest.", "group": "EXPLORATION", "id": null, "name": "resolve", "summary": "Resolve release version, tag, asset URL and digest.", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; resolve: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/resolve/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/run | invocation | run | command route; parser handles remaining tokens |  | Run declared project steps in the CALLER'S checkout (not an isolated worktree); configured default_steps when no --step is given. [mutating; dry-run] | Run declared project steps in the CALLER'S checkout (not an isolated worktree); configured default_steps when no --step is given. | MODIFICATION | mutating, dry-run | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; run: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/run/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/skills | delegate-group | skills | delegated group; child CLI parses remaining tokens | delegated: route:entrypoint:cmru/skills/install, route:entrypoint:cmru/skills/uninstall, route:entrypoint:cmru/skills/check, route:entrypoint:cmru/skills/list | install, check, or remove this tool's packaged agent skills | install, check, or remove this tool's packaged agent skills | MAINTENANCE |  | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | [] | incomplete |
| route:entrypoint:cmru/skills/check | invocation | skills check | command route; parser handles remaining tokens |  | exit 1 unless every packaged skill is current | exit 1 unless every packaged skill is current | EXPLORATION |  | false | {} | [{"behavior": [], "confirmation": false, "description": "install, check, or remove this tool's packaged agent skills", "group": "MAINTENANCE", "id": null, "name": "skills", "summary": "install, check, or remove this tool's packaged agent skills", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills check: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | [] | incomplete |
| route:entrypoint:cmru/skills/install | invocation | skills install | command route; parser handles remaining tokens |  | install or update the packaged skills, removing orphans [mutating; dry-run] | install or update the packaged skills, removing orphans | MODIFICATION | mutating, dry-run | false | {} | [{"behavior": [], "confirmation": false, "description": "install, check, or remove this tool's packaged agent skills", "group": "MAINTENANCE", "id": null, "name": "skills", "summary": "install, check, or remove this tool's packaged agent skills", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills install: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | [] | incomplete |
| route:entrypoint:cmru/skills/list | invocation | skills list | command route; parser handles remaining tokens |  | list packaged skills and their installed state | list packaged skills and their installed state | EXPLORATION |  | false | {} | [{"behavior": [], "confirmation": false, "description": "install, check, or remove this tool's packaged agent skills", "group": "MAINTENANCE", "id": null, "name": "skills", "summary": "install, check, or remove this tool's packaged agent skills", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills list: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | [] | incomplete |
| route:entrypoint:cmru/skills/uninstall | invocation | skills uninstall | command route; parser handles remaining tokens |  | remove the skills this tool installed [mutating; dry-run] | remove the skills this tool installed | MODIFICATION | mutating, dry-run | false | {} | [{"behavior": [], "confirmation": false, "description": "install, check, or remove this tool's packaged agent skills", "group": "MAINTENANCE", "id": null, "name": "skills", "summary": "install, check, or remove this tool's packaged agent skills", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills uninstall: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | [] | incomplete |
| route:entrypoint:cmru/standards | invocation | standards | single-command; empty remainder is parsed |  | Check or update CMRU framework markers. [mutating] | Check or update CMRU framework markers. | MIXED OPERATIONS | mutating | true | {} | [{"behavior": ["mutating", "dry-run"], "confirmation": false, "description": "Check or update CMRU-owned framework markers for projects.", "group": "MIXED OPERATIONS", "id": null, "name": "standards", "summary": "Check or update CMRU-owned framework markers for projects. [mutating; dry-run]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; standards: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/standards/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/status | invocation | status | command route; parser handles remaining tokens |  | Preview changed projects and their next versions (read-only). | Preview changed projects and their next versions (read-only). | EXPLORATION |  | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; status: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/status/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/tester-gate | invocation | tester-gate | single-command; empty remainder is parsed |  | Run one command inside tester-unified. [mutating] | Run one command inside tester-unified. | MODIFICATION | mutating | true | {} | [{"behavior": ["mutating", "dry-run"], "confirmation": false, "description": "Run the supplied command inside tester-unified with declared host limits.", "group": "MODIFICATION", "id": null, "name": "tester-gate", "summary": "Run the supplied command inside tester-unified with declared host limits. [mutating; dry-run]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; tester-gate: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/tester-gate/--cpus.type", "option:route:entrypoint:cmru/tester-gate/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/tool-deps | invocation | tool-deps | single-command; empty remainder is parsed |  | Verify declared tool dependency integrity, authenticity, and freshness. [mutating] | Verify declared tool dependency integrity, authenticity, and freshness. | MIXED OPERATIONS | mutating | true | {} | [{"behavior": ["mutating", "dry-run"], "confirmation": false, "description": "Verify declared dependencies or explicitly refresh one provider pin.", "group": "MIXED OPERATIONS", "id": null, "name": "tool-deps", "summary": "Verify declared dependencies or explicitly refresh one provider pin. [mutating; dry-run]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; tool-deps: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/tool-deps/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/versions | delegate-group | versions | delegated group; child CLI parses remaining tokens | delegated: route:entrypoint:cmru/versions/init, route:entrypoint:cmru/versions/resolve, route:entrypoint:cmru/versions/check | Derive, resolve, and check declared dependency versions. [mutating] | Derive, resolve, and check declared dependency versions. | MODIFICATION | mutating | true | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | [] | complete |
| route:entrypoint:cmru/versions/check | invocation | versions check | command route; parser handles remaining tokens |  | Compare recorded and currently eligible versions without writing. | Compare recorded and currently eligible versions without writing. | EXPLORATION |  | false | {} | [{"behavior": ["mutating"], "confirmation": true, "description": "Derive, resolve, and check declared dependency versions.", "group": "MODIFICATION", "id": null, "name": "versions", "summary": "Derive, resolve, and check declared dependency versions. [mutating]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; versions: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; versions check: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/versions/check/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/versions/init | invocation | versions init | command route; parser handles remaining tokens |  | Derive version targets from supported project manifests. [mutating; dry-run] | Derive version targets from supported project manifests. | MODIFICATION | mutating, dry-run | false | {} | [{"behavior": ["mutating"], "confirmation": true, "description": "Derive, resolve, and check declared dependency versions.", "group": "MODIFICATION", "id": null, "name": "versions", "summary": "Derive, resolve, and check declared dependency versions. [mutating]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; versions: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; versions init: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/versions/init/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/versions/resolve | invocation | versions resolve | command route; parser handles remaining tokens |  | Resolve declared versions and write their records and artifacts. [mutating; dry-run] | Resolve declared versions and write their records and artifacts. | MODIFICATION | mutating, dry-run | false | {} | [{"behavior": ["mutating"], "confirmation": true, "description": "Derive, resolve, and check declared dependency versions.", "group": "MODIFICATION", "id": null, "name": "versions", "summary": "Derive, resolve, and check declared dependency versions. [mutating]", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; versions: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; versions resolve: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/versions/resolve/--log-prefix-time-short.action"] | complete |
| route:entrypoint:cmru/worktrees | invocation | worktrees | command route; parser handles remaining tokens |  | Discover retained CMRU build and release worktrees (read-only). | Discover retained CMRU build and release worktrees (read-only). | EXPLORATION |  | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; worktrees: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:cmru/worktrees/--log-prefix-time-short.action"] | complete |

### Arguments and options

| Surface ID | Kind | Spelling/name | Description | Grammar shape | Action/type/const | Required | Choices/default/exclusive rule | Scope/placement | Visibility/help group |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| option:route:entrypoint:cmru/abandon/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["abandon"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/abandon/branch | argument | branch | exact managed build or release branch, or the path of its worktree; omit to select all retained release transactions | {"metavar": "BRANCH\|PATH", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["abandon"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/abandon/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["abandon"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/build/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["build"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/build/target | argument | target | project target; omitted: the current project, or every orchestrated project at the estate root | {"metavar": "[all\|PROJECT[,PROJECT...]]", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": {"all_token": "all", "callable": "cli_extended.SelectorList", "choices": null, "separator": ","}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["build"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/build/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["build"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/build/--show-run-details | option | --show-run-details | stream full project subprocess output | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["build"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/build/--log-append | option | --log-append | append a divider and retain existing stable per-step logs | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["build"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/changelog/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["changelog"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/changelog/target | argument | target | project target; omitted: the current project, or every orchestrated project at the estate root | {"metavar": "[all\|PROJECT[,PROJECT...]]", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": {"all_token": "all", "callable": "cli_extended.SelectorList", "choices": null, "separator": ","}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["changelog"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/changelog/--backfill-tag | option | --backfill-tag | tag to backfill; repeat once per selected project | {"metavar": "TAG", "minimum_values": 1} | {"action": "argparse._AppendAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["changelog"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/changelog/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["changelog"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/cleanup/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["cleanup"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/cleanup/target | argument | target | project target; omitted: the current project, or every orchestrated project at the estate root | {"metavar": "[all\|PROJECT[,PROJECT...]]", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": {"all_token": "all", "callable": "cli_extended.SelectorList", "choices": null, "separator": ","}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["cleanup"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/cleanup/--policy | option | --policy | apply the configured [cleanup] policy: delete Releases, tags and GHCR versions per project, run steps.clean, and commit | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": "cleanup-mode", "exclusive_required": true} | {"parser_path": ["cleanup"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/cleanup/--remove-assets | option | --remove-assets | age-based remote Releases and GHCR cleanup (estate-wide; takes no project target) | {"metavar": "AGE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "cmru.cli._non_empty"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "cleanup-mode", "exclusive_required": true} | {"parser_path": ["cleanup"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag | option | --delete-unmanaged-release-tag | delete one exact non-CMRU GitHub Release, never its Git tag | {"metavar": "TAG", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "cmru.cli._non_empty"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "cleanup-mode", "exclusive_required": true} | {"parser_path": ["cleanup"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/cleanup/--delete-build-output | option | --delete-build-output | delete one exact local build record | {"metavar": "ID", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "cmru.cli._non_empty"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "cleanup-mode", "exclusive_required": true} | {"parser_path": ["cleanup"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/cleanup/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["cleanup"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/dependencies/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["dependencies"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| option:route:entrypoint:cmru/dependencies/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["dependencies"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/dependencies/--write | option | --write | write the generated graph into the orchestration document | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["dependencies"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/doctor/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["doctor"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| option:route:entrypoint:cmru/doctor/--check | option | --check | run only this check (repeatable) | {"metavar": "NAME", "minimum_values": 1} | {"action": "argparse._AppendAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["doctor"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/get-py/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["get-py"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/get-py/target | argument | target | project target; omitted: the current project, or every orchestrated project at the estate root | {"metavar": "[all\|PROJECT[,PROJECT...]]", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": {"all_token": "all", "callable": "cli_extended.SelectorList", "choices": null, "separator": ","}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["get-py"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/get-py/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["get-py"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/get-py/--output | option | --output | write one selected installer to this file | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "destination", "exclusive_required": false} | {"parser_path": ["get-py"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/get-py/--output-dir | option | --output-dir | write one named installer per selected project | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "destination", "exclusive_required": false} | {"parser_path": ["get-py"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/bundle-manifest/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "bundle-manifest"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| option:route:entrypoint:cmru/handler/bundle-manifest/--name | option | --name | project name (as in cmru.toml) | {"metavar": "NAME", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "bundle-manifest"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/bundle-manifest/--tag | option | --tag | full release tag, e.g. tls-edge-v1.2.3 | {"metavar": "TAG", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "bundle-manifest"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/bundle-manifest/--root | option | --root | the staged bundle directory (the tarball's top-level dir) | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "bundle-manifest"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/bundle-manifest/--manifest-name | option | --manifest-name | manifest file name (default manifest.json) | {"metavar": "NAME", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": "manifest.json", "default": "manifest.json", "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "bundle-manifest"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/oci-image-build/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "oci-image-build"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| option:route:entrypoint:cmru/handler/oci-image-build/--cwd | option | --cwd | project directory (holds bake file) | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "oci-image-build"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/oci-image-build/--bake-file | option | --bake-file | path to bake HCL file | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "oci-image-build"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/oci-image-build/--bake-target | option | --bake-target | bake target name (not a project target) | {"metavar": "NAME", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "oci-image-build"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/oci-image-push/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "oci-image-push"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| option:route:entrypoint:cmru/handler/oci-image-push/--cwd | option | --cwd | project directory (holds bake file) | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "oci-image-push"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/oci-image-push/--bake-file | option | --bake-file | path to bake HCL file | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "oci-image-push"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/oci-image-push/--bake-target | option | --bake-target | bake target name (not a project target) | {"metavar": "NAME", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "oci-image-push"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/tarball-publish/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "tarball-publish"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| option:route:entrypoint:cmru/handler/tarball-publish/--prefix | option | --prefix | release prefix without -v | {"metavar": "PREFIX", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "tarball-publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/tarball-publish/--cwd | option | --cwd | project directory (dist/ holds the tarball) | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "tarball-publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/tarball-publish/--glob | option | --glob | tarball glob | {"metavar": "GLOB", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "tarball-publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/tarball-publish/--version-file | option | --version-file | version file relative to --cwd | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "version-source", "exclusive_required": true} | {"parser_path": ["handler", "tarball-publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/tarball-publish/--version-env | option | --version-env | environment variable containing the version | {"metavar": "NAME", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "version-source", "exclusive_required": true} | {"parser_path": ["handler", "tarball-publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/tarball-publish/--notes-env | option | --notes-env | environment variable holding optional release notes | {"metavar": "NAME", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "tarball-publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/tarball-validate/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "tarball-validate"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| option:route:entrypoint:cmru/handler/tarball-validate/--prefix | option | --prefix | release prefix without -v | {"metavar": "PREFIX", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "tarball-validate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/tarball-validate/--artifact-suffix | option | --artifact-suffix | expected artifact file extension | {"metavar": "SUFFIX", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": ".tar.xz", "default": ".tar.xz", "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "tarball-validate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/wheel-build/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "wheel-build"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| option:route:entrypoint:cmru/handler/wheel-build/--cwd | option | --cwd | project directory (holds pyproject.toml) | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "wheel-build"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/wheel-publish/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "wheel-publish"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| option:route:entrypoint:cmru/handler/wheel-publish/--prefix | option | --prefix | release prefix without -v | {"metavar": "PREFIX", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "wheel-publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/wheel-publish/--cwd | option | --cwd | project directory (dist/ holds the wheel) | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "wheel-publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/wheel-publish/--glob | option | --glob | wheel glob (default: <prefix>-*.whl) | {"metavar": "GLOB", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "wheel-publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/wheel-publish/--notes-env | option | --notes-env | environment variable holding release notes | {"metavar": "NAME", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "wheel-publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/wheel-publish/--extra-asset | option | --extra-asset | additional file to attach; repeatable | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._AppendAction", "const": null, "type": null} | false | {"choices": null, "declared_default": [], "default": [], "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "wheel-publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/handler/wheel-validate/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "wheel-validate"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| option:route:entrypoint:cmru/handler/wheel-validate/--prefix | option | --prefix | release prefix without -v | {"metavar": "PREFIX", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["handler", "wheel-validate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/init/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| option:route:entrypoint:cmru/init/--root | option | --root | adoption directory (default: current directory) | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/init/--layout | option | --layout | contract layout; a monorepo always asks for each project's facts | {"metavar": "LAYOUT", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["single", "monorepo"], "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/init/--owner | option | --owner | GitHub owner (default: detected from origin) | {"metavar": "OWNER", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/init/--repo | option | --repo | GitHub repository (default: detected from origin) | {"metavar": "REPO", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/init/--owner-type | option | --owner-type | GitHub owner type | {"metavar": "TYPE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["user", "org"], "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/init/--folder | option | --folder | single layout: the project folder inside --root (default: --root itself) | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/init/--id | option | --id | single layout: project id, lowercase (default: derived from the folder name) | {"metavar": "ID", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/init/--description | option | --description | single layout: one-line project description (default: 'The <id> project.') | {"metavar": "TEXT", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/init/--kind | option | --kind | single layout: python (wheel commands generated) or generic (you give the commands) | {"metavar": "KIND", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["python", "generic"], "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/init/--artifacts | option | --artifacts | single layout: comma-separated wheel\|tarball\|bundle\|oci-image, or all | {"metavar": "LIST", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/init/--release-tags | option | --release-tags | single layout: whether CMRU creates release tags | {"metavar": "yes\|no", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["yes", "no"], "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/init/--build-command | option | --build-command | single layout: build command as shell words (needed for generic projects or non-wheel artifacts) | {"metavar": "COMMAND", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/init/--publish-command | option | --publish-command | single layout: publish command as shell words (needed for generic projects or non-wheel artifacts) | {"metavar": "COMMAND", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/publish/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["publish"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/publish/target | argument | target | project target; omitted: the current project, or every orchestrated project at the estate root | {"metavar": "[all\|PROJECT[,PROJECT...]]", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": {"all_token": "all", "callable": "cli_extended.SelectorList", "choices": null, "separator": ","}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["publish"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/publish/--build-output | option | --build-output | publish the verified retained build ID printed by cmru build | {"metavar": "ID", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "cmru.cli._non_empty"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "publish-source", "exclusive_required": true} | {"parser_path": ["publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/publish/--from-checkout | option | --from-checkout | publish whatever the CALLER'S checkout currently holds (not isolated, not verified) | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": "publish-source", "exclusive_required": true} | {"parser_path": ["publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/publish/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/publish/--show-run-details | option | --show-run-details | stream full project subprocess output | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/publish/--log-append | option | --log-append | append a divider and retain existing stable per-step logs | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["publish"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/release/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/release/target | argument | target | project target; omitted: the current project, or every orchestrated project at the estate root | {"metavar": "[all\|PROJECT[,PROJECT...]]", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": {"all_token": "all", "callable": "cli_extended.SelectorList", "choices": null, "separator": ","}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["release"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/release/--minor | option | --minor | bump minor versions | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": "version-override", "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/release/--major | option | --major | bump major versions | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": "version-override", "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/release/--set-version | option | --set-version | set an explicit version (exactly one project must be selected) | {"metavar": "VER", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "version-override", "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/release/--no-build | option | --no-build | tag and push only; skip build and publish | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/release/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/release/--resume | option | --resume | resume a retained failed release worktree | {"metavar": "WORKTREE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/release/--allow-uncommitted | option | --allow-uncommitted | allow local edits to be omitted from the origin/main snapshot | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/release/--allow-tag-ahead-of-head | option | --allow-tag-ahead-of-head | allow a tag strictly ahead of the snapshot | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/release/--allow-stale-tool-deps | option | --allow-stale-tool-deps | allow declared tool dependencies behind their latest release | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/release/--show-run-details | option | --show-run-details | stream full project subprocess output | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/release/--log-append | option | --log-append | append a divider and retain existing stable per-step logs | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/release/--discard | option | --discard | do not retain this kind of output after a successful release; repeat for several | {"metavar": "{logs,artifacts,evidence}", "minimum_values": 1} | {"action": "argparse._AppendAction", "const": null, "type": null} | false | {"choices": ["logs", "artifacts", "evidence"], "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/release/--ahead-check-ref | option | --ahead-check-ref | git ref the local-ahead guard compares against (the snapshot is always origin/main) | {"metavar": "REF", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/release/--ref | option | --ref | DEPRECATED alias of --ahead-check-ref; removed in the next release | {"metavar": "REF", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["release"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/resolve/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["resolve"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/resolve/target | argument | target | project target; omitted: the current project, or every orchestrated project at the estate root | {"metavar": "[all\|PROJECT[,PROJECT...]]", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": {"all_token": "all", "callable": "cli_extended.SelectorList", "choices": null, "separator": ","}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["resolve"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/resolve/--format | option | --format | result format: json (object for one explicit project, map for 'all' or a list), env or url (default: json) | {"metavar": "FORMAT", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["json", "env", "url"], "declared_default": "json", "default": "json", "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["resolve"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/resolve/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["resolve"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/resolve/--repo | option | --repo | config-free mode: resolve in this GitHub repository (needs --prefix; token from $GITHUB_PUSH_PAT or $GITHUB_TOKEN, optional for public repos) | {"metavar": "OWNER/REPO", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["resolve"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/resolve/--prefix | option | --prefix | config-free mode: the release tag prefix, for example ciu-v (needs --repo) | {"metavar": "PREFIX", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["resolve"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/run/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["run"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/run/target | argument | target | project target; omitted: the current project, or every orchestrated project at the estate root | {"metavar": "[all\|PROJECT[,PROJECT...]]", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": {"all_token": "all", "callable": "cli_extended.SelectorList", "choices": null, "separator": ","}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["run"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/run/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["run"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/run/--step | option | --step | declared step to run; repeat for several, in the order given (default: the configured default_steps) | {"metavar": "NAME", "minimum_values": 1} | {"action": "argparse._AppendAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["run"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/run/--show-run-details | option | --show-run-details | stream full project subprocess output | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["run"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/run/--log-append | option | --log-append | append a divider and retain existing stable per-step logs | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["run"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/skills/check/--harness | option | --harness | target harness: claude, agents or all (default: all) | {"metavar": "HARNESS", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["claude", "agents", "all"], "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "check"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:cmru/skills/check/--dest | option | --dest | use exactly this directory instead of a harness directory | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "check"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:cmru/skills/install/--harness | option | --harness | target harness: claude, agents or all (default: all) | {"metavar": "HARNESS", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["claude", "agents", "all"], "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "install"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:cmru/skills/install/--dest | option | --dest | use exactly this directory instead of a harness directory | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "install"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:cmru/skills/install/--overwrite-modified | option | --overwrite-modified | replace skills you modified locally | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["skills", "install"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:cmru/skills/list/--harness | option | --harness | target harness: claude, agents or all (default: all) | {"metavar": "HARNESS", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["claude", "agents", "all"], "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "list"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:cmru/skills/list/--dest | option | --dest | use exactly this directory instead of a harness directory | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "list"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:cmru/skills/uninstall/--harness | option | --harness | target harness: claude, agents or all (default: all) | {"metavar": "HARNESS", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["claude", "agents", "all"], "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "uninstall"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:cmru/skills/uninstall/--dest | option | --dest | use exactly this directory instead of a harness directory | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "uninstall"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:cmru/skills/uninstall/--overwrite-modified | option | --overwrite-modified | replace skills you modified locally | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["skills", "uninstall"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:cmru/standards/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["standards"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/standards/target | argument | target | project target; omitted: the current project, or every orchestrated project at the estate root | {"metavar": "[all\|PROJECT[,PROJECT...]]", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": {"all_token": "all", "callable": "cli_extended.SelectorList", "choices": null, "separator": ","}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["standards"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/standards/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["standards"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/standards/--update | option | --update | update stale CMRU-owned revision markers | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["standards"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/status/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["status"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/status/target | argument | target | project target; omitted: the current project, or every orchestrated project at the estate root | {"metavar": "[all\|PROJECT[,PROJECT...]]", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": {"all_token": "all", "callable": "cli_extended.SelectorList", "choices": null, "separator": ","}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["status"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/status/--minor | option | --minor | bump minor versions | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": "version-override", "exclusive_required": false} | {"parser_path": ["status"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/status/--major | option | --major | bump major versions | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": "version-override", "exclusive_required": false} | {"parser_path": ["status"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/status/--set-version | option | --set-version | set an explicit version (exactly one project must be selected) | {"metavar": "VER", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "version-override", "exclusive_required": false} | {"parser_path": ["status"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/status/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["status"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/status/--ref | option | --ref | git ref used for status comparison | {"metavar": "REF", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["status"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/tester-gate/command | argument | command | command to execute in the gate container | {"metavar": "COMMAND", "minimum_values": 0, "nargs": "..."} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": [], "default": [], "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--cwd | option | --cwd | relative directory in the current worktree | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--image | option | --image | tester container image (default: $CMRU_TESTER_UNIFIED_IMAGE) | {"metavar": "IMG", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--cgroup-parent | option | --cgroup-parent | explicit host gates slice, verified before launch (default: $CMRU_TESTER_CGROUP_PARENT) | {"metavar": "SLICE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--forward-background-slice | option | --forward-background-slice | slice forwarded into the container as $CGROUP_PARENT_DEV_BACKGROUND (default: $CMRU_TESTER_CGROUP_FORWARD_VAR) | {"metavar": "SLICE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--forward-gates-slice | option | --forward-gates-slice | slice forwarded into the container as $CGROUP_PARENT_DEV_GATES (default: $CMRU_TESTER_CGROUP_FORWARD_GATES_VAR) | {"metavar": "SLICE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--memory | option | --memory | Docker memory cap (default: $CMRU_TESTER_MEMORY) | {"metavar": "MEMORY", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--memory-swap | option | --memory-swap | Docker combined memory-plus-swap total (default: $CMRU_TESTER_MEMORY_SWAP) | {"metavar": "MEMORY", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--cpus | option | --cpus | CPU ceiling >= 0.00001 (default: $CMRU_TESTER_CPUS) | {"metavar": "N", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "cmru.tester_gate._positive_cpu_limit"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--pids-limit | option | --pids-limit | container process ceiling, a positive integer (default: $CMRU_TESTER_PIDS_LIMIT) | {"metavar": "N", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--cgroup-probe-image | option | --cgroup-probe-image | digest-pinned host-systemd probe image (default: $CMRU_TESTER_CGROUP_PROBE_IMAGE) | {"metavar": "IMG", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--dind-image | option | --dind-image | digest-pinned nested Docker daemon image; with --enable-docker (default: $CMRU_TESTER_DIND_IMAGE) | {"metavar": "IMG", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--dind-memory | option | --dind-memory | nested Docker memory cap; with --enable-docker (default: $CMRU_TESTER_DIND_MEMORY) | {"metavar": "MEMORY", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--dind-cpus | option | --dind-cpus | nested Docker CPU ceiling; with --enable-docker (default: $CMRU_TESTER_DIND_CPUS) | {"metavar": "N", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--dind-pids-limit | option | --dind-pids-limit | nested Docker process ceiling; with --enable-docker (default: $CMRU_TESTER_DIND_PIDS_LIMIT) | {"metavar": "N", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--device-read-iops | option | --device-read-iops | per-container read IOPS cap, Docker path:rate (default: $CMRU_TESTER_DEVICE_READ_IOPS, none when unset) | {"metavar": "DEV:RATE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--device-write-iops | option | --device-write-iops | per-container write IOPS cap, Docker path:rate (default: $CMRU_TESTER_DEVICE_WRITE_IOPS, none when unset) | {"metavar": "DEV:RATE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--device-read-bps | option | --device-read-bps | per-container read bandwidth cap, Docker path:rate (default: $CMRU_TESTER_DEVICE_READ_BPS, none when unset) | {"metavar": "DEV:RATE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--device-write-bps | option | --device-write-bps | per-container write bandwidth cap, Docker path:rate (default: $CMRU_TESTER_DEVICE_WRITE_BPS, none when unset) | {"metavar": "DEV:RATE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tester-gate/--enable-docker | option | --enable-docker | give this step an isolated nested Docker daemon | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tester-gate"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tool-deps/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tool-deps"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/tool-deps/target | argument | target | project target; omitted: the current project, or every orchestrated project at the estate root | {"metavar": "[all\|PROJECT[,PROJECT...]]", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": {"all_token": "all", "callable": "cli_extended.SelectorList", "choices": null, "separator": ","}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tool-deps"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/tool-deps/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tool-deps"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tool-deps/--allow-stale-tool-deps | option | --allow-stale-tool-deps | allow staleness only; integrity and authenticity failures remain errors | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tool-deps"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tool-deps/--refresh | option | --refresh | re-vendor this provider's latest artifact and rewrite the selected pin | {"metavar": "PROVIDER_PROJECT", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tool-deps"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/tool-deps/--timeout | option | --timeout | network timeout for each GitHub request | {"metavar": "SECONDS", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "builtins.int"}} | false | {"choices": null, "declared_default": 10, "default": 10, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tool-deps"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": true}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/versions/check/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["versions", "check"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/versions/check/target | argument | target | project target; omitted: the current project, or every orchestrated project at the estate root | {"metavar": "[all\|PROJECT[,PROJECT...]]", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": {"all_token": "all", "callable": "cli_extended.SelectorList", "choices": null, "separator": ","}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["versions", "check"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/versions/check/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["versions", "check"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/versions/init/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["versions", "init"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/versions/init/target | argument | target | project target; omitted: the current project, or every orchestrated project at the estate root | {"metavar": "[all\|PROJECT[,PROJECT...]]", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": {"all_token": "all", "callable": "cli_extended.SelectorList", "choices": null, "separator": ","}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["versions", "init"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/versions/init/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["versions", "init"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/versions/resolve/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["versions", "resolve"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |
| argument:route:entrypoint:cmru/versions/resolve/target | argument | target | project target; omitted: the current project, or every orchestrated project at the estate root | {"metavar": "[all\|PROJECT[,PROJECT...]]", "minimum_values": 0, "nargs": "?"} | {"action": "argparse._StoreAction", "const": null, "type": {"all_token": "all", "callable": "cli_extended.SelectorList", "choices": null, "separator": ","}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["versions", "resolve"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:cmru/versions/resolve/--config | option | --config | path to cmru.toml or cmru.orchestration.toml | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["versions", "resolve"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OPTIONS", "hidden": false} |
| option:route:entrypoint:cmru/worktrees/--log-prefix-time-short | option | --log-prefix-time-short | prefix severity lines with local HH:MM:SS timestamps | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "cmru.cli_support._ShortTimePrefixAction", "const": null, "type": null} | false | {"choices": null, "declared_default": {"kind": "suppressed"}, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["worktrees"], "placement": {"after_verb": true, "before_verb": true, "single_command_invocation": false}, "scope": "global"} | {"help_group": "OUTPUT CONTROL", "hidden": false} |

### Library common controls

- `route:entrypoint:cmru/abandon`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version, --yes
- `route:entrypoint:cmru/build`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/changelog`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/cleanup`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version, --yes
- `route:entrypoint:cmru/dependencies`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --json, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/doctor`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/get-py`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/handler`: Common controls: none
- `route:entrypoint:cmru/handler/bundle-manifest`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/handler/oci-image-build`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/handler/oci-image-push`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/handler/tarball-publish`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/handler/tarball-validate`: Common controls: --color, --debug, --debug-raw, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/handler/wheel-build`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/handler/wheel-publish`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/handler/wheel-validate`: Common controls: --color, --debug, --debug-raw, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/init`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version, --yes
- `route:entrypoint:cmru/publish`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/release`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/resolve`: Common controls: --color, --debug, --debug-raw, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/run`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/skills`: Common controls: none
- `route:entrypoint:cmru/skills/check`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --progress, --quiet, --traceback, --version
- `route:entrypoint:cmru/skills/install`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --progress, --quiet, --traceback, --version
- `route:entrypoint:cmru/skills/list`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --progress, --quiet, --traceback, --version
- `route:entrypoint:cmru/skills/uninstall`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --progress, --quiet, --traceback, --version
- `route:entrypoint:cmru/standards`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --json, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/status`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/tester-gate`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/tool-deps`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --json, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/versions`: Common controls: none
- `route:entrypoint:cmru/versions/check`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/versions/init`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/versions/resolve`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:cmru/worktrees`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --quiet, --traceback, --version

### Constraints

- `route:entrypoint:cmru/dependencies`:
  - --dry-run requires --write: a dependency preview is only meaningful for the --write block
- `route:entrypoint:cmru/get-py`:
  - --dry-run requires --output or --output-dir: stdout mode writes nothing, so there is nothing to preview
- `route:entrypoint:cmru/resolve`:
  - --repo and --config cannot be used together: config-free mode reads no configuration
  - --repo requires --prefix: a repository needs the tag prefix to resolve
  - --prefix requires --repo: a prefix needs the repository to resolve in
- `route:entrypoint:cmru/standards`:
  - --dry-run requires --update: without --update the check writes nothing, so there is nothing to preview
- `route:entrypoint:cmru/tool-deps`:
  - --dry-run requires --refresh: without --refresh the check writes nothing, so there is nothing to preview
  - --refresh and --json cannot be used together: a refresh prints progress lines, not a report
  - --refresh and --allow-stale-tool-deps cannot be used together: a refresh re-vendors the latest artifact, so staleness cannot be allowed

### Semantic case review

The invocation is an argv token list passed to the registered CLI, excluding the executable name. Test IDs prove collection/linkage only; the normal gate proves execution and assertions.

| Case ID | Dimension | Surface IDs / shape | Decision | Invocation | Expected status/output | Effects | Test IDs | Rationale/state |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| case:route:entrypoint:cmru/abandon/argument-shape/argument:route:entrypoint:cmru/abandon/branch | argument-shape | {"members": ["argument:route:entrypoint:cmru/abandon/branch"], "shape": {"argument_id": "argument:route:entrypoint:cmru/abandon/branch"}} | ACCEPT | abandon demo-branch | {"status": 2, "stderr contains": "cannot inspect CMRU release worktrees", "stdout contains": ""} | Outside a git repository discovery refuses (exit 2) before any branch lookup; nothing is read from the network and nothing is changed. | tests/test_cli_review_cases.py::test_reviewed_case[abandon/argument-shape/branch] | The optional branch names exactly one managed release or build branch (or its worktree path). A value is accepted by the parser; resolution against the retained worktrees happens after discovery. |
| case:route:entrypoint:cmru/abandon/minimum | minimum | {"members": ["option:route:entrypoint:cmru/abandon/--log-prefix-time-short"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/abandon/--log-prefix-time-short"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | abandon | {"status": 2, "stderr contains": "cannot inspect CMRU release worktrees", "stdout contains": ""} | Outside a git repository discovery refuses (exit 2); with a repository it would list candidates and require confirmation before any deletion. | tests/test_cli_review_cases.py::test_reviewed_case[abandon/minimum] | No argument or option is required: bare `abandon` selects the complete retained release set and then asks for confirmation. It is a MAINTENANCE verb, so omission means the whole set, never a silent default write. |
| case:route:entrypoint:cmru/abandon/option-spelling/option:route:entrypoint:cmru/abandon/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/abandon/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/abandon/--config", "spelling": "--config"}} | ACCEPT | abandon --config missing.toml | {"status": 2, "stderr contains": "cannot inspect CMRU release worktrees", "stdout contains": ""} | Outside a git repository discovery refuses (exit 2) before the config is read; nothing is changed. | tests/test_cli_review_cases.py::test_reviewed_case[abandon/option-spelling/--config/--config] | --config PATH selects the project policy used to validate each recorded release scope; the long spelling is exact (no abbreviation). |
| case:route:entrypoint:cmru/abandon/option-spelling/option:route:entrypoint:cmru/abandon/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/abandon/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/abandon/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | abandon --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the unredacted-diagnostics warning on stderr, then refuses at worktree discovery (exit 2); nothing is changed. | tests/test_cli_review_cases.py::test_reviewed_case[abandon/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts redaction of diagnostics and always prints a warning that secrets may be exposed. |
| case:route:entrypoint:cmru/abandon/option-spelling/option:route:entrypoint:cmru/abandon/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/abandon/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/abandon/--dry-run", "spelling": "--dry-run"}} | ACCEPT | abandon --dry-run | {"status": 2, "stderr contains": "cannot inspect CMRU release worktrees", "stdout contains": ""} | Outside a git repository discovery refuses (exit 2); in a repository the preview deletes nothing. | tests/test_cli_review_cases.py::test_reviewed_case[abandon/option-spelling/--dry-run/--dry-run] | --dry-run reports the exact local and known remote candidates without changing them; it is the safe preview before --yes. |
| case:route:entrypoint:cmru/abandon/option-spelling/option:route:entrypoint:cmru/abandon/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/abandon/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/abandon/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | abandon --log-prefix-time-short | {"status": 2, "stderr contains": "cannot inspect CMRU release worktrees", "stdout contains": ""} | Presentation only; the refusal at worktree discovery (exit 2) is the same and nothing is changed. | tests/test_cli_review_cases.py::test_reviewed_case[abandon/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option: prefixes severity lines with local HH:MM:SS; it changes output only. |
| case:route:entrypoint:cmru/abandon/option-spelling/option:route:entrypoint:cmru/abandon/--yes/--yes | option-spelling | {"members": ["option:route:entrypoint:cmru/abandon/--yes"], "shape": {"option_id": "option:route:entrypoint:cmru/abandon/--yes", "spelling": "--yes"}} | ACCEPT | abandon --yes | {"status": 2, "stderr contains": "cannot inspect CMRU release worktrees", "stdout contains": ""} | Outside a git repository discovery refuses (exit 2) before anything is displayed or confirmed; nothing is deleted. | tests/test_cli_review_cases.py::test_reviewed_case[abandon/option-spelling/--yes/--yes] | --yes confirms the complete displayed set non-interactively; without it interactive confirmation is required. |
| case:route:entrypoint:cmru/build/argument-shape/argument:route:entrypoint:cmru/build/target | argument-shape | {"members": ["argument:route:entrypoint:cmru/build/target"], "shape": {"argument_id": "argument:route:entrypoint:cmru/build/target"}} | ACCEPT | build demo | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no snapshot is taken and nothing is built. | tests/test_cli_review_cases.py::test_reviewed_case[build/argument-shape/target] | The optional target is all\|PROJECT[,PROJECT...]; omitted means the current project or every orchestrated project at the estate root. |
| case:route:entrypoint:cmru/build/minimum | minimum | {"members": ["option:route:entrypoint:cmru/build/--log-prefix-time-short", "option:route:entrypoint:cmru/build/--show-run-details", "option:route:entrypoint:cmru/build/--log-append"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/build/--log-prefix-time-short", "option:route:entrypoint:cmru/build/--show-run-details", "option:route:entrypoint:cmru/build/--log-append"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | build | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Without a cmru config the verb refuses (exit 2) before any snapshot or step runs. | tests/test_cli_review_cases.py::test_reviewed_case[build/minimum] | Nothing is required: a bare `build` builds the current project from an isolated snapshot. |
| case:route:entrypoint:cmru/build/option-spelling/option:route:entrypoint:cmru/build/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/build/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/build/--config", "spelling": "--config"}} | REFUSE | build --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2) before reading anything; nothing is built. | tests/test_cli_review_cases.py::test_reviewed_case[build/option-spelling/--config/--config] | --config must name a file called cmru.toml or cmru.orchestration.toml; any other name is refused so a wrong file is never read as a contract. |
| case:route:entrypoint:cmru/build/option-spelling/option:route:entrypoint:cmru/build/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/build/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/build/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | build --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the unredacted-diagnostics warning, then stops at configuration discovery (exit 2); nothing is built. | tests/test_cli_review_cases.py::test_reviewed_case[build/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/build/option-spelling/option:route:entrypoint:cmru/build/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/build/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/build/--dry-run", "spelling": "--dry-run"}} | ACCEPT | build --dry-run | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); with a config the preview starts no project command. | tests/test_cli_review_cases.py::test_reviewed_case[build/option-spelling/--dry-run/--dry-run] | --dry-run prints the declared prepare/gate/build plan without starting project commands. |
| case:route:entrypoint:cmru/build/option-spelling/option:route:entrypoint:cmru/build/--log-append/--log-append | option-spelling | {"members": ["option:route:entrypoint:cmru/build/--log-append"], "shape": {"option_id": "option:route:entrypoint:cmru/build/--log-append", "spelling": "--log-append"}} | ACCEPT | build --log-append | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no log file is opened. | tests/test_cli_review_cases.py::test_reviewed_case[build/option-spelling/--log-append/--log-append] | --log-append retains prior step logs instead of truncating them. |
| case:route:entrypoint:cmru/build/option-spelling/option:route:entrypoint:cmru/build/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/build/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/build/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | build --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[build/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option: timestamps severity lines; output only. |
| case:route:entrypoint:cmru/build/option-spelling/option:route:entrypoint:cmru/build/--show-run-details/--show-run-details | option-spelling | {"members": ["option:route:entrypoint:cmru/build/--show-run-details"], "shape": {"option_id": "option:route:entrypoint:cmru/build/--show-run-details", "spelling": "--show-run-details"}} | ACCEPT | build --show-run-details | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no step runs so nothing streams. | tests/test_cli_review_cases.py::test_reviewed_case[build/option-spelling/--show-run-details/--show-run-details] | --show-run-details streams subprocess output of each step instead of only the summary. |
| case:route:entrypoint:cmru/changelog/argument-shape/argument:route:entrypoint:cmru/changelog/target | argument-shape | {"members": ["argument:route:entrypoint:cmru/changelog/target"], "shape": {"argument_id": "argument:route:entrypoint:cmru/changelog/target"}} | ACCEPT | changelog --backfill-tag demo-v1.0.0 demo | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no changelog file is written. | tests/test_cli_review_cases.py::test_reviewed_case[changelog/argument-shape/target] | The optional target selects projects; one --backfill-tag per selected project is then required and ambiguous tag/project matches are refused. |
| case:route:entrypoint:cmru/changelog/minimum | minimum | {"members": ["option:route:entrypoint:cmru/changelog/--log-prefix-time-short", "option:route:entrypoint:cmru/changelog/--backfill-tag"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/changelog/--log-prefix-time-short"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": ["option:route:entrypoint:cmru/changelog/--backfill-tag"]}} | ACCEPT | changelog --backfill-tag demo-v1.0.0 | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is read from the network or written. | tests/test_cli_review_cases.py::test_reviewed_case[changelog/minimum] | --backfill-tag TAG is the one required input: a post-release migration names an already-published tag. |
| case:route:entrypoint:cmru/changelog/option-spelling/option:route:entrypoint:cmru/changelog/--backfill-tag/--backfill-tag | option-spelling | {"members": ["option:route:entrypoint:cmru/changelog/--backfill-tag"], "shape": {"option_id": "option:route:entrypoint:cmru/changelog/--backfill-tag", "spelling": "--backfill-tag"}} | ACCEPT | changelog --backfill-tag demo-v1.0.0 | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no file is written. | tests/test_cli_review_cases.py::test_reviewed_case[changelog/option-spelling/--backfill-tag/--backfill-tag] | Repeatable --backfill-tag TAG provides one already-published tag per selected project. |
| case:route:entrypoint:cmru/changelog/option-spelling/option:route:entrypoint:cmru/changelog/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/changelog/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/changelog/--config", "spelling": "--config"}} | REFUSE | changelog --backfill-tag demo-v1.0.0 --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2); nothing is read or written. | tests/test_cli_review_cases.py::test_reviewed_case[changelog/option-spelling/--config/--config] | --config must name cmru.toml or cmru.orchestration.toml; other names are refused. |
| case:route:entrypoint:cmru/changelog/option-spelling/option:route:entrypoint:cmru/changelog/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/changelog/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/changelog/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | changelog --backfill-tag demo-v1.0.0 --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[changelog/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/changelog/option-spelling/option:route:entrypoint:cmru/changelog/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/changelog/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/changelog/--dry-run", "spelling": "--dry-run"}} | ACCEPT | changelog --backfill-tag demo-v1.0.0 --dry-run | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); with a config it would print a diff only. | tests/test_cli_review_cases.py::test_reviewed_case[changelog/option-spelling/--dry-run/--dry-run] | --dry-run prints the exact changelog diff and writes no file. |
| case:route:entrypoint:cmru/changelog/option-spelling/option:route:entrypoint:cmru/changelog/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/changelog/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/changelog/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | changelog --backfill-tag demo-v1.0.0 --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[changelog/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/cleanup/argument-shape/argument:route:entrypoint:cmru/cleanup/target | argument-shape | {"members": ["argument:route:entrypoint:cmru/cleanup/target"], "shape": {"argument_id": "argument:route:entrypoint:cmru/cleanup/target"}} | ACCEPT | cleanup --policy demo | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is planned or deleted. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/argument-shape/target] | The optional target selects projects for --policy; --remove-assets refuses a project target (estate-wide only). |
| case:route:entrypoint:cmru/cleanup/exclusive-conflict/cleanup-mode/option:route:entrypoint:cmru/cleanup/--delete-build-output/option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/cleanup/--delete-build-output", "option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag"], "shape": {"group_id": "cleanup-mode", "options": ["option:route:entrypoint:cmru/cleanup/--delete-build-output", "option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag"]}} | REFUSE | cleanup --delete-build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --delete-unmanaged-release-tag demo-v1.0.0 | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing is read or deleted. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/exclusive-conflict/cleanup-mode/--delete-build-output/--delete-unmanaged-release-tag] | Cleanup modes are mutually exclusive: one run performs exactly one kind of deletion. |
| case:route:entrypoint:cmru/cleanup/exclusive-conflict/cleanup-mode/option:route:entrypoint:cmru/cleanup/--delete-build-output/option:route:entrypoint:cmru/cleanup/--policy | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/cleanup/--delete-build-output", "option:route:entrypoint:cmru/cleanup/--policy"], "shape": {"group_id": "cleanup-mode", "options": ["option:route:entrypoint:cmru/cleanup/--delete-build-output", "option:route:entrypoint:cmru/cleanup/--policy"]}} | REFUSE | cleanup --delete-build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --policy | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing is read or deleted. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/exclusive-conflict/cleanup-mode/--delete-build-output/--policy] | Cleanup modes are mutually exclusive: one run performs exactly one kind of deletion. |
| case:route:entrypoint:cmru/cleanup/exclusive-conflict/cleanup-mode/option:route:entrypoint:cmru/cleanup/--delete-build-output/option:route:entrypoint:cmru/cleanup/--remove-assets | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/cleanup/--delete-build-output", "option:route:entrypoint:cmru/cleanup/--remove-assets"], "shape": {"group_id": "cleanup-mode", "options": ["option:route:entrypoint:cmru/cleanup/--delete-build-output", "option:route:entrypoint:cmru/cleanup/--remove-assets"]}} | REFUSE | cleanup --delete-build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --remove-assets 30d | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing is read or deleted. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/exclusive-conflict/cleanup-mode/--delete-build-output/--remove-assets] | Cleanup modes are mutually exclusive: one run performs exactly one kind of deletion. |
| case:route:entrypoint:cmru/cleanup/exclusive-conflict/cleanup-mode/option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag/option:route:entrypoint:cmru/cleanup/--policy | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag", "option:route:entrypoint:cmru/cleanup/--policy"], "shape": {"group_id": "cleanup-mode", "options": ["option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag", "option:route:entrypoint:cmru/cleanup/--policy"]}} | REFUSE | cleanup --delete-unmanaged-release-tag demo-v1.0.0 --policy | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing is read or deleted. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/exclusive-conflict/cleanup-mode/--delete-unmanaged-release-tag/--policy] | Cleanup modes are mutually exclusive: one run performs exactly one kind of deletion. |
| case:route:entrypoint:cmru/cleanup/exclusive-conflict/cleanup-mode/option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag/option:route:entrypoint:cmru/cleanup/--remove-assets | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag", "option:route:entrypoint:cmru/cleanup/--remove-assets"], "shape": {"group_id": "cleanup-mode", "options": ["option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag", "option:route:entrypoint:cmru/cleanup/--remove-assets"]}} | REFUSE | cleanup --delete-unmanaged-release-tag demo-v1.0.0 --remove-assets 30d | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing is read or deleted. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/exclusive-conflict/cleanup-mode/--delete-unmanaged-release-tag/--remove-assets] | Cleanup modes are mutually exclusive: one run performs exactly one kind of deletion. |
| case:route:entrypoint:cmru/cleanup/exclusive-conflict/cleanup-mode/option:route:entrypoint:cmru/cleanup/--policy/option:route:entrypoint:cmru/cleanup/--remove-assets | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/cleanup/--policy", "option:route:entrypoint:cmru/cleanup/--remove-assets"], "shape": {"group_id": "cleanup-mode", "options": ["option:route:entrypoint:cmru/cleanup/--policy", "option:route:entrypoint:cmru/cleanup/--remove-assets"]}} | REFUSE | cleanup --policy --remove-assets 30d | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing is read or deleted. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/exclusive-conflict/cleanup-mode/--policy/--remove-assets] | Cleanup modes are mutually exclusive: one run performs exactly one kind of deletion. |
| case:route:entrypoint:cmru/cleanup/exclusive-member/cleanup-mode/option:route:entrypoint:cmru/cleanup/--delete-build-output | exclusive-member | {"members": ["option:route:entrypoint:cmru/cleanup/--delete-build-output"], "shape": {"group_id": "cleanup-mode", "required": true, "selected_option": "option:route:entrypoint:cmru/cleanup/--delete-build-output"}} | ACCEPT | cleanup --delete-build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no retained record is touched. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/exclusive-member/cleanup-mode/--delete-build-output] | --delete-build-output ID deletes the exact validated local retained build record; it satisfies the required mode on its own. |
| case:route:entrypoint:cmru/cleanup/exclusive-member/cleanup-mode/option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag | exclusive-member | {"members": ["option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag"], "shape": {"group_id": "cleanup-mode", "required": true, "selected_option": "option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag"}} | ACCEPT | cleanup --delete-unmanaged-release-tag demo-v1.0.0 | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no remote request is made. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/exclusive-member/cleanup-mode/--delete-unmanaged-release-tag] | --delete-unmanaged-release-tag TAG deletes one exact unmanaged GitHub Release but never its Git tag. |
| case:route:entrypoint:cmru/cleanup/exclusive-member/cleanup-mode/option:route:entrypoint:cmru/cleanup/--policy | exclusive-member | {"members": ["option:route:entrypoint:cmru/cleanup/--policy"], "shape": {"group_id": "cleanup-mode", "required": true, "selected_option": "option:route:entrypoint:cmru/cleanup/--policy"}} | ACCEPT | cleanup --policy | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no plan is displayed and nothing is deleted. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/exclusive-member/cleanup-mode/--policy] | --policy applies the configured [cleanup] policy to the resolved scope (Releases, tags, GHCR versions, steps.clean, commit) after displaying the captured plan. |
| case:route:entrypoint:cmru/cleanup/exclusive-member/cleanup-mode/option:route:entrypoint:cmru/cleanup/--remove-assets | exclusive-member | {"members": ["option:route:entrypoint:cmru/cleanup/--remove-assets"], "shape": {"group_id": "cleanup-mode", "required": true, "selected_option": "option:route:entrypoint:cmru/cleanup/--remove-assets"}} | ACCEPT | cleanup --remove-assets 30d | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no remote request is made. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/exclusive-member/cleanup-mode/--remove-assets] | --remove-assets AGE prunes age-based remote Releases/tags/GHCR versions estate-wide under the [cleanup] policy; a project target is refused. |
| case:route:entrypoint:cmru/cleanup/minimum | minimum | {"members": ["option:route:entrypoint:cmru/cleanup/--log-prefix-time-short", "option:route:entrypoint:cmru/cleanup/--policy", "option:route:entrypoint:cmru/cleanup/--policy", "option:route:entrypoint:cmru/cleanup/--remove-assets", "option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag", "option:route:entrypoint:cmru/cleanup/--delete-build-output"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/cleanup/--log-prefix-time-short", "option:route:entrypoint:cmru/cleanup/--policy"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {"cleanup-mode": ["option:route:entrypoint:cmru/cleanup/--policy", "option:route:entrypoint:cmru/cleanup/--remove-assets", "option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag", "option:route:entrypoint:cmru/cleanup/--delete-build-output"]}, "required_options": []}} | ACCEPT | cleanup --policy | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is deleted. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/minimum] | Exactly one explicit mode is required; the minimum is one mode flag (a bare `cleanup` is refused with the mode list, exit 2, and is not presented as routine). |
| case:route:entrypoint:cmru/cleanup/option-spelling/option:route:entrypoint:cmru/cleanup/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/cleanup/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/cleanup/--config", "spelling": "--config"}} | REFUSE | cleanup --policy --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2); nothing is deleted. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/option-spelling/--config/--config] | --config selects the policy file; only cmru.toml or cmru.orchestration.toml are accepted names. |
| case:route:entrypoint:cmru/cleanup/option-spelling/option:route:entrypoint:cmru/cleanup/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/cleanup/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/cleanup/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | cleanup --policy --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/cleanup/option-spelling/option:route:entrypoint:cmru/cleanup/--delete-build-output/--delete-build-output | option-spelling | {"members": ["option:route:entrypoint:cmru/cleanup/--delete-build-output"], "shape": {"option_id": "option:route:entrypoint:cmru/cleanup/--delete-build-output", "spelling": "--delete-build-output"}} | ACCEPT | cleanup --delete-build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is deleted. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/option-spelling/--delete-build-output/--delete-build-output] | Exact spelling of the build-output deletion mode; takes one non-empty ID. |
| case:route:entrypoint:cmru/cleanup/option-spelling/option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag/--delete-unmanaged-release-tag | option-spelling | {"members": ["option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag"], "shape": {"option_id": "option:route:entrypoint:cmru/cleanup/--delete-unmanaged-release-tag", "spelling": "--delete-unmanaged-release-tag"}} | ACCEPT | cleanup --delete-unmanaged-release-tag demo-v1.0.0 | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no remote request is made. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/option-spelling/--delete-unmanaged-release-tag/--delete-unmanaged-release-tag] | Exact spelling of the unmanaged-release-tag deletion mode; takes one non-empty TAG. |
| case:route:entrypoint:cmru/cleanup/option-spelling/option:route:entrypoint:cmru/cleanup/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/cleanup/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/cleanup/--dry-run", "spelling": "--dry-run"}} | ACCEPT | cleanup --policy --dry-run | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); with a config it deletes nothing. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/option-spelling/--dry-run/--dry-run] | --dry-run displays the captured target plan and performs no deletion. |
| case:route:entrypoint:cmru/cleanup/option-spelling/option:route:entrypoint:cmru/cleanup/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/cleanup/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/cleanup/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | cleanup --policy --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/cleanup/option-spelling/option:route:entrypoint:cmru/cleanup/--policy/--policy | option-spelling | {"members": ["option:route:entrypoint:cmru/cleanup/--policy"], "shape": {"option_id": "option:route:entrypoint:cmru/cleanup/--policy", "spelling": "--policy"}} | ACCEPT | cleanup --policy | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is deleted. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/option-spelling/--policy/--policy] | Exact spelling of the configured-policy mode. |
| case:route:entrypoint:cmru/cleanup/option-spelling/option:route:entrypoint:cmru/cleanup/--remove-assets/--remove-assets | option-spelling | {"members": ["option:route:entrypoint:cmru/cleanup/--remove-assets"], "shape": {"option_id": "option:route:entrypoint:cmru/cleanup/--remove-assets", "spelling": "--remove-assets"}} | ACCEPT | cleanup --remove-assets 30d | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no remote request is made. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/option-spelling/--remove-assets/--remove-assets] | Exact spelling of the age-based remote cleanup mode; takes one AGE. |
| case:route:entrypoint:cmru/cleanup/option-spelling/option:route:entrypoint:cmru/cleanup/--yes/--yes | option-spelling | {"members": ["option:route:entrypoint:cmru/cleanup/--yes"], "shape": {"option_id": "option:route:entrypoint:cmru/cleanup/--yes", "spelling": "--yes"}} | ACCEPT | cleanup --policy --yes | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2) before any plan exists; nothing is deleted. | tests/test_cli_review_cases.py::test_reviewed_case[cleanup/option-spelling/--yes/--yes] | --yes accepts the captured plan without rediscovery or an interactive prompt. |
| case:route:entrypoint:cmru/dependencies/constraint-requires/1 | constraint-requires | {"members": ["option:route:entrypoint:cmru/dependencies/--dry-run", "option:route:entrypoint:cmru/dependencies/--write"], "shape": {"any_of": ["--write"], "kind": "requires", "option": "--dry-run", "reason": "a dependency preview is only meaningful for the --write block"}} | REFUSE | dependencies --dry-run | {"status": 2, "stderr contains": "--dry-run requires --write", "stdout contains": ""} | Refused as a usage error (exit 2) before configuration is read; nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[dependencies/constraint-requires/1] | A dependency preview is only meaningful for the --write block: --dry-run without --write is refused instead of silently reading. |
| case:route:entrypoint:cmru/dependencies/minimum | minimum | {"members": ["option:route:entrypoint:cmru/dependencies/--log-prefix-time-short", "option:route:entrypoint:cmru/dependencies/--write"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/dependencies/--log-prefix-time-short", "option:route:entrypoint:cmru/dependencies/--write"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | dependencies | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at orchestration-file discovery (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[dependencies/minimum] | Bare `dependencies` is the read-only graph report; --write is the opt-in mutation (mixed-operations verb). |
| case:route:entrypoint:cmru/dependencies/option-spelling/option:route:entrypoint:cmru/dependencies/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/dependencies/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/dependencies/--config", "spelling": "--config"}} | REFUSE | dependencies --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[dependencies/option-spelling/--config/--config] | --config selects the orchestration file; only the two canonical names are accepted. |
| case:route:entrypoint:cmru/dependencies/option-spelling/option:route:entrypoint:cmru/dependencies/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/dependencies/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/dependencies/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | dependencies --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[dependencies/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/dependencies/option-spelling/option:route:entrypoint:cmru/dependencies/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/dependencies/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/dependencies/--dry-run", "spelling": "--dry-run"}} | ACCEPT | dependencies --write --dry-run | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at orchestration-file discovery (exit 2); with a file the preview writes nothing. | tests/test_cli_review_cases.py::test_reviewed_case[dependencies/option-spelling/--dry-run/--dry-run] | With --write, --dry-run prints the exact diff of the marked generated graph block without writing. |
| case:route:entrypoint:cmru/dependencies/option-spelling/option:route:entrypoint:cmru/dependencies/--json/--json | option-spelling | {"members": ["option:route:entrypoint:cmru/dependencies/--json"], "shape": {"option_id": "option:route:entrypoint:cmru/dependencies/--json", "spelling": "--json"}} | ACCEPT | dependencies --json | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at orchestration-file discovery (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[dependencies/option-spelling/--json/--json] | --json selects machine-readable output of the same graph (a real output mode, not a mutation selector); it may accompany --write. |
| case:route:entrypoint:cmru/dependencies/option-spelling/option:route:entrypoint:cmru/dependencies/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/dependencies/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/dependencies/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | dependencies --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[dependencies/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/dependencies/option-spelling/option:route:entrypoint:cmru/dependencies/--write/--write | option-spelling | {"members": ["option:route:entrypoint:cmru/dependencies/--write"], "shape": {"option_id": "option:route:entrypoint:cmru/dependencies/--write", "spelling": "--write"}} | ACCEPT | dependencies --write | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at orchestration-file discovery (exit 2); no file is written. | tests/test_cli_review_cases.py::test_reviewed_case[dependencies/option-spelling/--write/--write] | --write updates only the marked generated graph block; it is the single opt-in mutation of this verb. |
| case:route:entrypoint:cmru/doctor/minimum | minimum | {"members": ["option:route:entrypoint:cmru/doctor/--log-prefix-time-short"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/doctor/--log-prefix-time-short"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | doctor | {"status": 1, "stderr contains": "", "stdout contains": "[FAIL] docker"} | Reads PATH tools and environment names only, never prints a credential value, changes nothing. | tests/test_cli_review_cases.py::test_reviewed_case[doctor/minimum] | Bare `doctor` runs every read-only check; exit 1 only when a check is `fail` (here the stub docker daemon is unreachable); warn and skip exit 0. |
| case:route:entrypoint:cmru/doctor/option-spelling/option:route:entrypoint:cmru/doctor/--check/--check | option-spelling | {"members": ["option:route:entrypoint:cmru/doctor/--check"], "shape": {"option_id": "option:route:entrypoint:cmru/doctor/--check", "spelling": "--check"}} | ACCEPT | doctor --check git | {"status": 0, "stderr contains": "", "stdout contains": "[OK] git"} | Runs only the git check (git --version); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[doctor/option-spelling/--check/--check] | Repeatable --check NAME runs only the named checks. |
| case:route:entrypoint:cmru/doctor/option-spelling/option:route:entrypoint:cmru/doctor/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/doctor/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/doctor/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | doctor --debug-raw | {"status": 1, "stderr contains": "--debug-raw is active", "stdout contains": "[OK] git"} | Prints the warning; checks stay read-only (docker unreachable in the sandbox gives exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[doctor/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/doctor/option-spelling/option:route:entrypoint:cmru/doctor/--json/--json | option-spelling | {"members": ["option:route:entrypoint:cmru/doctor/--json"], "shape": {"option_id": "option:route:entrypoint:cmru/doctor/--json", "spelling": "--json"}} | ACCEPT | doctor --json | {"status": 1, "stderr contains": "", "stdout contains": "\"checks\""} | Read-only; machine output only. | tests/test_cli_review_cases.py::test_reviewed_case[doctor/option-spelling/--json/--json] | --json emits {tool, version, checks, summary} on stdout; the exit status rule is unchanged. |
| case:route:entrypoint:cmru/doctor/option-spelling/option:route:entrypoint:cmru/doctor/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/doctor/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/doctor/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | doctor --log-prefix-time-short | {"status": 1, "stderr contains": "", "stdout contains": "[OK] git"} | Presentation only; checks are read-only. | tests/test_cli_review_cases.py::test_reviewed_case[doctor/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/get-py/argument-shape/argument:route:entrypoint:cmru/get-py/target | argument-shape | {"members": ["argument:route:entrypoint:cmru/get-py/target"], "shape": {"argument_id": "argument:route:entrypoint:cmru/get-py/target"}} | ACCEPT | get-py demo | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no installer is rendered. | tests/test_cli_review_cases.py::test_reviewed_case[get-py/argument-shape/target] | The optional target selects installer templates; several selected projects without --output-dir are refused (exit 2). |
| case:route:entrypoint:cmru/get-py/constraint-requires/1 | constraint-requires | {"members": ["option:route:entrypoint:cmru/get-py/--dry-run", "option:route:entrypoint:cmru/get-py/--output", "option:route:entrypoint:cmru/get-py/--output-dir"], "shape": {"any_of": ["--output", "--output-dir"], "kind": "requires", "option": "--dry-run", "reason": "stdout mode writes nothing, so there is nothing to preview"}} | REFUSE | get-py --dry-run | {"status": 2, "stderr contains": "--dry-run requires --output or --output-dir", "stdout contains": ""} | Refused as a usage error (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[get-py/constraint-requires/1] | Stdout mode writes nothing, so a dry-run preview needs a destination: --dry-run requires --output or --output-dir. |
| case:route:entrypoint:cmru/get-py/exclusive-conflict/destination/option:route:entrypoint:cmru/get-py/--output/option:route:entrypoint:cmru/get-py/--output-dir | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/get-py/--output", "option:route:entrypoint:cmru/get-py/--output-dir"], "shape": {"group_id": "destination", "options": ["option:route:entrypoint:cmru/get-py/--output", "option:route:entrypoint:cmru/get-py/--output-dir"]}} | REFUSE | get-py --output out.py --output-dir outdir | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[get-py/exclusive-conflict/destination/--output/--output-dir] | --output (one installer) and --output-dir (one per project) are mutually exclusive destinations. |
| case:route:entrypoint:cmru/get-py/exclusive-member/destination/option:route:entrypoint:cmru/get-py/--output | exclusive-member | {"members": ["option:route:entrypoint:cmru/get-py/--output"], "shape": {"group_id": "destination", "required": false, "selected_option": "option:route:entrypoint:cmru/get-py/--output"}} | ACCEPT | get-py --output out.py | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); out.py is not created. | tests/test_cli_review_cases.py::test_reviewed_case[get-py/exclusive-member/destination/--output] | --output FILE writes exactly one selected installer. |
| case:route:entrypoint:cmru/get-py/exclusive-member/destination/option:route:entrypoint:cmru/get-py/--output-dir | exclusive-member | {"members": ["option:route:entrypoint:cmru/get-py/--output-dir"], "shape": {"group_id": "destination", "required": false, "selected_option": "option:route:entrypoint:cmru/get-py/--output-dir"}} | ACCEPT | get-py --output-dir outdir | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no directory is created. | tests/test_cli_review_cases.py::test_reviewed_case[get-py/exclusive-member/destination/--output-dir] | --output-dir DIR writes one installer per selected project. |
| case:route:entrypoint:cmru/get-py/minimum | minimum | {"members": ["option:route:entrypoint:cmru/get-py/--log-prefix-time-short"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/get-py/--log-prefix-time-short"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | get-py | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is rendered. | tests/test_cli_review_cases.py::test_reviewed_case[get-py/minimum] | Omitting both destinations prints ONE project's installer to stdout; the verb never writes unasked. |
| case:route:entrypoint:cmru/get-py/option-spelling/option:route:entrypoint:cmru/get-py/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/get-py/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/get-py/--config", "spelling": "--config"}} | REFUSE | get-py --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2); nothing is rendered. | tests/test_cli_review_cases.py::test_reviewed_case[get-py/option-spelling/--config/--config] | --config selects the template contract; only the two canonical names are accepted. |
| case:route:entrypoint:cmru/get-py/option-spelling/option:route:entrypoint:cmru/get-py/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/get-py/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/get-py/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | get-py --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[get-py/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/get-py/option-spelling/option:route:entrypoint:cmru/get-py/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/get-py/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/get-py/--dry-run", "spelling": "--dry-run"}} | ACCEPT | get-py --output out.py --dry-run | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); with a config the preview writes nothing. | tests/test_cli_review_cases.py::test_reviewed_case[get-py/option-spelling/--dry-run/--dry-run] | With a destination, --dry-run renders and validates the templates and destinations but writes no file. |
| case:route:entrypoint:cmru/get-py/option-spelling/option:route:entrypoint:cmru/get-py/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/get-py/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/get-py/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | get-py --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[get-py/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/get-py/option-spelling/option:route:entrypoint:cmru/get-py/--output-dir/--output-dir | option-spelling | {"members": ["option:route:entrypoint:cmru/get-py/--output-dir"], "shape": {"option_id": "option:route:entrypoint:cmru/get-py/--output-dir", "spelling": "--output-dir"}} | ACCEPT | get-py --output-dir outdir | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no directory is created. | tests/test_cli_review_cases.py::test_reviewed_case[get-py/option-spelling/--output-dir/--output-dir] | Exact spelling of the per-project destination (distinct from --output). |
| case:route:entrypoint:cmru/get-py/option-spelling/option:route:entrypoint:cmru/get-py/--output/--output | option-spelling | {"members": ["option:route:entrypoint:cmru/get-py/--output"], "shape": {"option_id": "option:route:entrypoint:cmru/get-py/--output", "spelling": "--output"}} | ACCEPT | get-py --output out.py | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); out.py is not created. | tests/test_cli_review_cases.py::test_reviewed_case[get-py/option-spelling/--output/--output] | Exact spelling of the single-file destination. |
| case:route:entrypoint:cmru/handler/bundle-manifest/minimum | minimum | {"members": ["option:route:entrypoint:cmru/handler/bundle-manifest/--log-prefix-time-short", "option:route:entrypoint:cmru/handler/bundle-manifest/--name", "option:route:entrypoint:cmru/handler/bundle-manifest/--tag", "option:route:entrypoint:cmru/handler/bundle-manifest/--root", "option:route:entrypoint:cmru/handler/bundle-manifest/--manifest-name"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/handler/bundle-manifest/--log-prefix-time-short", "option:route:entrypoint:cmru/handler/bundle-manifest/--manifest-name"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": ["option:route:entrypoint:cmru/handler/bundle-manifest/--name", "option:route:entrypoint:cmru/handler/bundle-manifest/--tag", "option:route:entrypoint:cmru/handler/bundle-manifest/--root"]}} | ACCEPT | handler bundle-manifest --name demo --tag v1.0.0 --root missing-dir | {"status": 3, "stderr contains": "SOURCE_DATE_EPOCH is not set", "stdout contains": ""} | Refuses before reading or writing the bundle root; nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[handler/bundle-manifest/minimum] | --name, --tag and --root are required; it writes manifest.json into the staged bundle root. Without SOURCE_DATE_EPOCH it refuses as a missing prerequisite (exit 3, one line, no traceback; fixed in W2-PKG5). |
| case:route:entrypoint:cmru/handler/bundle-manifest/option-spelling/option:route:entrypoint:cmru/handler/bundle-manifest/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/bundle-manifest/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/bundle-manifest/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | handler bundle-manifest --name demo --tag v1.0.0 --root missing-dir --debug-raw | {"status": 3, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then refuses on the missing SOURCE_DATE_EPOCH (exit 3); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[handler/bundle-manifest/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/handler/bundle-manifest/option-spelling/option:route:entrypoint:cmru/handler/bundle-manifest/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/bundle-manifest/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/bundle-manifest/--dry-run", "spelling": "--dry-run"}} | ACCEPT | handler bundle-manifest --name demo --tag v1.0.0 --root missing-dir --dry-run | {"status": 0, "stderr contains": "", "stdout contains": "[DRY RUN] Would run cmru handler bundle-manifest"} | Prints the inputs only; the bundle root is not read and no manifest is written. | tests/test_cli_review_cases.py::test_reviewed_case[handler/bundle-manifest/option-spelling/--dry-run/--dry-run] | --dry-run shows the accepted inputs and writes nothing. |
| case:route:entrypoint:cmru/handler/bundle-manifest/option-spelling/option:route:entrypoint:cmru/handler/bundle-manifest/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/bundle-manifest/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/bundle-manifest/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | handler bundle-manifest --name demo --tag v1.0.0 --root missing-dir --log-prefix-time-short | {"status": 3, "stderr contains": "SOURCE_DATE_EPOCH is not set", "stdout contains": ""} | Presentation only; refuses on the missing SOURCE_DATE_EPOCH (exit 3). | tests/test_cli_review_cases.py::test_reviewed_case[handler/bundle-manifest/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/handler/bundle-manifest/option-spelling/option:route:entrypoint:cmru/handler/bundle-manifest/--manifest-name/--manifest-name | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/bundle-manifest/--manifest-name"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/bundle-manifest/--manifest-name", "spelling": "--manifest-name"}} | ACCEPT | handler bundle-manifest --name demo --tag v1.0.0 --root missing-dir --manifest-name manifest.json | {"status": 3, "stderr contains": "SOURCE_DATE_EPOCH is not set", "stdout contains": ""} | Refuses on the missing SOURCE_DATE_EPOCH (exit 3); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[handler/bundle-manifest/option-spelling/--manifest-name/--manifest-name] | --manifest-name NAME is the file name written inside --root (default manifest.json); it is not the project name. |
| case:route:entrypoint:cmru/handler/bundle-manifest/option-spelling/option:route:entrypoint:cmru/handler/bundle-manifest/--name/--name | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/bundle-manifest/--name"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/bundle-manifest/--name", "spelling": "--name"}} | ACCEPT | handler bundle-manifest --name demo --tag v1.0.0 --root missing-dir | {"status": 3, "stderr contains": "SOURCE_DATE_EPOCH is not set", "stdout contains": ""} | Refuses on the missing SOURCE_DATE_EPOCH (exit 3); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[handler/bundle-manifest/option-spelling/--name/--name] | --name is the project the manifest describes (not the manifest file name). |
| case:route:entrypoint:cmru/handler/bundle-manifest/option-spelling/option:route:entrypoint:cmru/handler/bundle-manifest/--root/--root | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/bundle-manifest/--root"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/bundle-manifest/--root", "spelling": "--root"}} | ACCEPT | handler bundle-manifest --name demo --tag v1.0.0 --root missing-dir | {"status": 3, "stderr contains": "SOURCE_DATE_EPOCH is not set", "stdout contains": ""} | Refuses on the missing SOURCE_DATE_EPOCH (exit 3) before the root is read. | tests/test_cli_review_cases.py::test_reviewed_case[handler/bundle-manifest/option-spelling/--root/--root] | --root is the staged bundle directory that becomes the tarball's single top-level directory; symlinks or special files inside are an error. |
| case:route:entrypoint:cmru/handler/bundle-manifest/option-spelling/option:route:entrypoint:cmru/handler/bundle-manifest/--tag/--tag | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/bundle-manifest/--tag"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/bundle-manifest/--tag", "spelling": "--tag"}} | ACCEPT | handler bundle-manifest --name demo --tag v1.0.0 --root missing-dir | {"status": 3, "stderr contains": "SOURCE_DATE_EPOCH is not set", "stdout contains": ""} | A valid tag passes the grammar check, then refuses on the missing SOURCE_DATE_EPOCH (exit 3). | tests/test_cli_review_cases.py::test_reviewed_case[handler/bundle-manifest/option-spelling/--tag/--tag] | --tag must be inside the installer's tag grammar (otherwise exit 2). |
| case:route:entrypoint:cmru/handler/oci-image-build/minimum | minimum | {"members": ["option:route:entrypoint:cmru/handler/oci-image-build/--log-prefix-time-short", "option:route:entrypoint:cmru/handler/oci-image-build/--cwd", "option:route:entrypoint:cmru/handler/oci-image-build/--bake-file", "option:route:entrypoint:cmru/handler/oci-image-build/--bake-target"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/handler/oci-image-build/--log-prefix-time-short"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": ["option:route:entrypoint:cmru/handler/oci-image-build/--cwd", "option:route:entrypoint:cmru/handler/oci-image-build/--bake-file", "option:route:entrypoint:cmru/handler/oci-image-build/--bake-target"]}} | ACCEPT | handler oci-image-build --cwd missing-dir --bake-file missing.hcl --bake-target demo | {"status": 3, "stderr contains": "docker buildx is required", "stdout contains": ""} | Prerequisite check fails (stub docker has no buildx): exit 3 before login or any build. | tests/test_cli_review_cases.py::test_reviewed_case[handler/oci-image-build/minimum] | --cwd, --bake-file and --bake-target (a docker bake target, not a project target) are required. |
| case:route:entrypoint:cmru/handler/oci-image-build/option-spelling/option:route:entrypoint:cmru/handler/oci-image-build/--bake-file/--bake-file | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/oci-image-build/--bake-file"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/oci-image-build/--bake-file", "spelling": "--bake-file"}} | ACCEPT | handler oci-image-build --cwd missing-dir --bake-file missing.hcl --bake-target demo | {"status": 3, "stderr contains": "docker buildx is required", "stdout contains": ""} | Prerequisite check fails first (exit 3); no build. | tests/test_cli_review_cases.py::test_reviewed_case[handler/oci-image-build/option-spelling/--bake-file/--bake-file] | --bake-file PATH names the docker bake file. |
| case:route:entrypoint:cmru/handler/oci-image-build/option-spelling/option:route:entrypoint:cmru/handler/oci-image-build/--bake-target/--bake-target | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/oci-image-build/--bake-target"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/oci-image-build/--bake-target", "spelling": "--bake-target"}} | ACCEPT | handler oci-image-build --cwd missing-dir --bake-file missing.hcl --bake-target demo | {"status": 3, "stderr contains": "docker buildx is required", "stdout contains": ""} | Prerequisite check fails first (exit 3); no build. | tests/test_cli_review_cases.py::test_reviewed_case[handler/oci-image-build/option-spelling/--bake-target/--bake-target] | --bake-target NAME is a docker bake target (it replaced the ambiguous --target). |
| case:route:entrypoint:cmru/handler/oci-image-build/option-spelling/option:route:entrypoint:cmru/handler/oci-image-build/--cwd/--cwd | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/oci-image-build/--cwd"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/oci-image-build/--cwd", "spelling": "--cwd"}} | ACCEPT | handler oci-image-build --cwd missing-dir --bake-file missing.hcl --bake-target demo | {"status": 3, "stderr contains": "docker buildx is required", "stdout contains": ""} | Prerequisite check fails first (exit 3); the directory is not entered. | tests/test_cli_review_cases.py::test_reviewed_case[handler/oci-image-build/option-spelling/--cwd/--cwd] | --cwd PATH is the project tree the build runs in. |
| case:route:entrypoint:cmru/handler/oci-image-build/option-spelling/option:route:entrypoint:cmru/handler/oci-image-build/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/oci-image-build/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/oci-image-build/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | handler oci-image-build --cwd missing-dir --bake-file missing.hcl --bake-target demo --debug-raw | {"status": 3, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning; the prerequisite check then fails (exit 3). | tests/test_cli_review_cases.py::test_reviewed_case[handler/oci-image-build/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/handler/oci-image-build/option-spelling/option:route:entrypoint:cmru/handler/oci-image-build/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/oci-image-build/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/oci-image-build/--dry-run", "spelling": "--dry-run"}} | ACCEPT | handler oci-image-build --cwd missing-dir --bake-file missing.hcl --bake-target demo --dry-run | {"status": 0, "stderr contains": "", "stdout contains": "[DRY RUN] Would run cmru handler oci-image-build"} | Prints the inputs only; no docker command runs. | tests/test_cli_review_cases.py::test_reviewed_case[handler/oci-image-build/option-spelling/--dry-run/--dry-run] | --dry-run previews the build without docker login or any command. |
| case:route:entrypoint:cmru/handler/oci-image-build/option-spelling/option:route:entrypoint:cmru/handler/oci-image-build/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/oci-image-build/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/oci-image-build/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | handler oci-image-build --cwd missing-dir --bake-file missing.hcl --bake-target demo --log-prefix-time-short | {"status": 3, "stderr contains": "docker buildx is required", "stdout contains": ""} | Presentation only; the prerequisite check fails (exit 3). | tests/test_cli_review_cases.py::test_reviewed_case[handler/oci-image-build/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/handler/oci-image-push/minimum | minimum | {"members": ["option:route:entrypoint:cmru/handler/oci-image-push/--log-prefix-time-short", "option:route:entrypoint:cmru/handler/oci-image-push/--cwd", "option:route:entrypoint:cmru/handler/oci-image-push/--bake-file", "option:route:entrypoint:cmru/handler/oci-image-push/--bake-target"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/handler/oci-image-push/--log-prefix-time-short"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": ["option:route:entrypoint:cmru/handler/oci-image-push/--cwd", "option:route:entrypoint:cmru/handler/oci-image-push/--bake-file", "option:route:entrypoint:cmru/handler/oci-image-push/--bake-target"]}} | ACCEPT | handler oci-image-push --cwd missing-dir --bake-file missing.hcl --bake-target demo | {"status": 1, "stderr contains": "REGISTRY is required", "stdout contains": ""} | Refuses on the unset REGISTRY environment variable (exit 1) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[handler/oci-image-push/minimum] | --cwd, --bake-file and --bake-target are required; push uses buildx bake push and needs REGISTRY. |
| case:route:entrypoint:cmru/handler/oci-image-push/option-spelling/option:route:entrypoint:cmru/handler/oci-image-push/--bake-file/--bake-file | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/oci-image-push/--bake-file"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/oci-image-push/--bake-file", "spelling": "--bake-file"}} | ACCEPT | handler oci-image-push --cwd missing-dir --bake-file missing.hcl --bake-target demo | {"status": 1, "stderr contains": "REGISTRY is required", "stdout contains": ""} | Refuses on the unset REGISTRY (exit 1); nothing is pushed. | tests/test_cli_review_cases.py::test_reviewed_case[handler/oci-image-push/option-spelling/--bake-file/--bake-file] | --bake-file PATH names the docker bake file. |
| case:route:entrypoint:cmru/handler/oci-image-push/option-spelling/option:route:entrypoint:cmru/handler/oci-image-push/--bake-target/--bake-target | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/oci-image-push/--bake-target"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/oci-image-push/--bake-target", "spelling": "--bake-target"}} | ACCEPT | handler oci-image-push --cwd missing-dir --bake-file missing.hcl --bake-target demo | {"status": 1, "stderr contains": "REGISTRY is required", "stdout contains": ""} | Refuses on the unset REGISTRY (exit 1); nothing is pushed. | tests/test_cli_review_cases.py::test_reviewed_case[handler/oci-image-push/option-spelling/--bake-target/--bake-target] | --bake-target NAME is a docker bake target, not a project target. |
| case:route:entrypoint:cmru/handler/oci-image-push/option-spelling/option:route:entrypoint:cmru/handler/oci-image-push/--cwd/--cwd | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/oci-image-push/--cwd"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/oci-image-push/--cwd", "spelling": "--cwd"}} | ACCEPT | handler oci-image-push --cwd missing-dir --bake-file missing.hcl --bake-target demo | {"status": 1, "stderr contains": "REGISTRY is required", "stdout contains": ""} | Refuses on the unset REGISTRY (exit 1); the directory is not entered. | tests/test_cli_review_cases.py::test_reviewed_case[handler/oci-image-push/option-spelling/--cwd/--cwd] | --cwd PATH is the project tree the push runs in. |
| case:route:entrypoint:cmru/handler/oci-image-push/option-spelling/option:route:entrypoint:cmru/handler/oci-image-push/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/oci-image-push/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/oci-image-push/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | handler oci-image-push --cwd missing-dir --bake-file missing.hcl --bake-target demo --debug-raw | {"status": 1, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then refuses on the unset REGISTRY (exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[handler/oci-image-push/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/handler/oci-image-push/option-spelling/option:route:entrypoint:cmru/handler/oci-image-push/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/oci-image-push/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/oci-image-push/--dry-run", "spelling": "--dry-run"}} | ACCEPT | handler oci-image-push --cwd missing-dir --bake-file missing.hcl --bake-target demo --dry-run | {"status": 0, "stderr contains": "", "stdout contains": "[DRY RUN] Would run cmru handler oci-image-push"} | Prints the inputs only; nothing is pushed. | tests/test_cli_review_cases.py::test_reviewed_case[handler/oci-image-push/option-spelling/--dry-run/--dry-run] | --dry-run shows the inputs without pushing. |
| case:route:entrypoint:cmru/handler/oci-image-push/option-spelling/option:route:entrypoint:cmru/handler/oci-image-push/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/oci-image-push/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/oci-image-push/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | handler oci-image-push --cwd missing-dir --bake-file missing.hcl --bake-target demo --log-prefix-time-short | {"status": 1, "stderr contains": "REGISTRY is required", "stdout contains": ""} | Presentation only; refuses on the unset REGISTRY (exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[handler/oci-image-push/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/handler/tarball-publish/exclusive-conflict/version-source/option:route:entrypoint:cmru/handler/tarball-publish/--version-env/option:route:entrypoint:cmru/handler/tarball-publish/--version-file | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/handler/tarball-publish/--version-env", "option:route:entrypoint:cmru/handler/tarball-publish/--version-file"], "shape": {"group_id": "version-source", "options": ["option:route:entrypoint:cmru/handler/tarball-publish/--version-env", "option:route:entrypoint:cmru/handler/tarball-publish/--version-file"]}} | REFUSE | handler tarball-publish --prefix demo-v --cwd missing-dir --glob '*.whl' --version-env VER --version-file VERSION | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing is published. | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-publish/exclusive-conflict/version-source/--version-env/--version-file] | Exactly one version source: --version-file and --version-env are mutually exclusive. |
| case:route:entrypoint:cmru/handler/tarball-publish/exclusive-member/version-source/option:route:entrypoint:cmru/handler/tarball-publish/--version-env | exclusive-member | {"members": ["option:route:entrypoint:cmru/handler/tarball-publish/--version-env"], "shape": {"group_id": "version-source", "required": true, "selected_option": "option:route:entrypoint:cmru/handler/tarball-publish/--version-env"}} | ACCEPT | handler tarball-publish --prefix demo-v --cwd missing-dir --glob '*.whl' --version-env VER | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1) before any network call. | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-publish/exclusive-member/version-source/--version-env] | --version-env NAME reads the version from the named environment variable. |
| case:route:entrypoint:cmru/handler/tarball-publish/exclusive-member/version-source/option:route:entrypoint:cmru/handler/tarball-publish/--version-file | exclusive-member | {"members": ["option:route:entrypoint:cmru/handler/tarball-publish/--version-file"], "shape": {"group_id": "version-source", "required": true, "selected_option": "option:route:entrypoint:cmru/handler/tarball-publish/--version-file"}} | ACCEPT | handler tarball-publish --prefix demo-v --cwd missing-dir --glob '*.whl' --version-file VERSION | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1) before any network call. | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-publish/exclusive-member/version-source/--version-file] | --version-file PATH reads the version from the named file. |
| case:route:entrypoint:cmru/handler/tarball-publish/minimum | minimum | {"members": ["option:route:entrypoint:cmru/handler/tarball-publish/--log-prefix-time-short", "option:route:entrypoint:cmru/handler/tarball-publish/--prefix", "option:route:entrypoint:cmru/handler/tarball-publish/--cwd", "option:route:entrypoint:cmru/handler/tarball-publish/--glob", "option:route:entrypoint:cmru/handler/tarball-publish/--version-file", "option:route:entrypoint:cmru/handler/tarball-publish/--version-env"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/handler/tarball-publish/--log-prefix-time-short"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {"version-source": ["option:route:entrypoint:cmru/handler/tarball-publish/--version-file", "option:route:entrypoint:cmru/handler/tarball-publish/--version-env"]}, "required_options": ["option:route:entrypoint:cmru/handler/tarball-publish/--prefix", "option:route:entrypoint:cmru/handler/tarball-publish/--cwd", "option:route:entrypoint:cmru/handler/tarball-publish/--glob"]}} | ACCEPT | handler tarball-publish --prefix demo-v --cwd missing-dir --glob '*.whl' --version-file VERSION | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1) before any network call. | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-publish/minimum] | --prefix, --cwd, --glob and one version source are required. |
| case:route:entrypoint:cmru/handler/tarball-publish/option-spelling/option:route:entrypoint:cmru/handler/tarball-publish/--cwd/--cwd | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/tarball-publish/--cwd"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/tarball-publish/--cwd", "spelling": "--cwd"}} | ACCEPT | handler tarball-publish --prefix demo-v --cwd missing-dir --glob '*.whl' --version-file VERSION | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1) before the tree is read. | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-publish/option-spelling/--cwd/--cwd] | --cwd PATH is the source tree the glob is resolved in. |
| case:route:entrypoint:cmru/handler/tarball-publish/option-spelling/option:route:entrypoint:cmru/handler/tarball-publish/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/tarball-publish/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/tarball-publish/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | handler tarball-publish --prefix demo-v --cwd missing-dir --glob '*.whl' --version-file VERSION --debug-raw | {"status": 1, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then refuses on the missing credential (exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-publish/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/handler/tarball-publish/option-spelling/option:route:entrypoint:cmru/handler/tarball-publish/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/tarball-publish/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/tarball-publish/--dry-run", "spelling": "--dry-run"}} | ACCEPT | handler tarball-publish --prefix demo-v --cwd missing-dir --glob '*.whl' --version-file VERSION --dry-run | {"status": 0, "stderr contains": "", "stdout contains": "[DRY RUN] Would run cmru handler tarball-publish"} | Prints the inputs only; nothing is published and no credential is needed. | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-publish/option-spelling/--dry-run/--dry-run] | --dry-run displays the inputs and skips publication. |
| case:route:entrypoint:cmru/handler/tarball-publish/option-spelling/option:route:entrypoint:cmru/handler/tarball-publish/--glob/--glob | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/tarball-publish/--glob"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/tarball-publish/--glob", "spelling": "--glob"}} | ACCEPT | handler tarball-publish --prefix demo-v --cwd missing-dir --glob '*.whl' --version-file VERSION | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1) before any glob is evaluated. | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-publish/option-spelling/--glob/--glob] | --glob GLOB selects the one source artifact (required for tarballs). |
| case:route:entrypoint:cmru/handler/tarball-publish/option-spelling/option:route:entrypoint:cmru/handler/tarball-publish/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/tarball-publish/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/tarball-publish/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | handler tarball-publish --prefix demo-v --cwd missing-dir --glob '*.whl' --version-file VERSION --log-prefix-time-short | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Presentation only; refuses on the missing credential (exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-publish/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/handler/tarball-publish/option-spelling/option:route:entrypoint:cmru/handler/tarball-publish/--notes-env/--notes-env | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/tarball-publish/--notes-env"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/tarball-publish/--notes-env", "spelling": "--notes-env"}} | ACCEPT | handler tarball-publish --prefix demo-v --cwd missing-dir --glob '*.whl' --version-file VERSION --notes-env NOTES | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1) before the variable is read. | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-publish/option-spelling/--notes-env/--notes-env] | --notes-env NAME names the environment variable holding release notes. |
| case:route:entrypoint:cmru/handler/tarball-publish/option-spelling/option:route:entrypoint:cmru/handler/tarball-publish/--prefix/--prefix | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/tarball-publish/--prefix"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/tarball-publish/--prefix", "spelling": "--prefix"}} | ACCEPT | handler tarball-publish --prefix demo-v --cwd missing-dir --glob '*.whl' --version-file VERSION | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1) before any network call. | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-publish/option-spelling/--prefix/--prefix] | --prefix selects the release namespace. |
| case:route:entrypoint:cmru/handler/tarball-publish/option-spelling/option:route:entrypoint:cmru/handler/tarball-publish/--version-env/--version-env | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/tarball-publish/--version-env"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/tarball-publish/--version-env", "spelling": "--version-env"}} | ACCEPT | handler tarball-publish --prefix demo-v --cwd missing-dir --glob '*.whl' --version-env VER | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-publish/option-spelling/--version-env/--version-env] | Exact spelling of the environment version source. |
| case:route:entrypoint:cmru/handler/tarball-publish/option-spelling/option:route:entrypoint:cmru/handler/tarball-publish/--version-file/--version-file | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/tarball-publish/--version-file"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/tarball-publish/--version-file", "spelling": "--version-file"}} | ACCEPT | handler tarball-publish --prefix demo-v --cwd missing-dir --glob '*.whl' --version-file VERSION | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-publish/option-spelling/--version-file/--version-file] | Exact spelling of the file version source. |
| case:route:entrypoint:cmru/handler/tarball-validate/minimum | minimum | {"members": ["option:route:entrypoint:cmru/handler/tarball-validate/--log-prefix-time-short", "option:route:entrypoint:cmru/handler/tarball-validate/--prefix", "option:route:entrypoint:cmru/handler/tarball-validate/--artifact-suffix"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/handler/tarball-validate/--log-prefix-time-short", "option:route:entrypoint:cmru/handler/tarball-validate/--artifact-suffix"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": ["option:route:entrypoint:cmru/handler/tarball-validate/--prefix"]}} | ACCEPT | handler tarball-validate --prefix demo-v | {"status": 1, "stderr contains": "GITHUB_USERNAME is required", "stdout contains": ""} | Refuses on the unset GITHUB_USERNAME (exit 1) before any network call. | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-validate/minimum] | --prefix selects the release to validate; the verb is read-only (no --dry-run) and needs GITHUB_USERNAME. |
| case:route:entrypoint:cmru/handler/tarball-validate/option-spelling/option:route:entrypoint:cmru/handler/tarball-validate/--artifact-suffix/--artifact-suffix | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/tarball-validate/--artifact-suffix"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/tarball-validate/--artifact-suffix", "spelling": "--artifact-suffix"}} | ACCEPT | handler tarball-validate --prefix demo-v --artifact-suffix .tar.xz | {"status": 1, "stderr contains": "GITHUB_USERNAME is required", "stdout contains": ""} | Refuses on the unset GITHUB_USERNAME (exit 1); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-validate/option-spelling/--artifact-suffix/--artifact-suffix] | --artifact-suffix SUFFIX is the expected extension (default .tar.xz). |
| case:route:entrypoint:cmru/handler/tarball-validate/option-spelling/option:route:entrypoint:cmru/handler/tarball-validate/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/tarball-validate/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/tarball-validate/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | handler tarball-validate --prefix demo-v --debug-raw | {"status": 1, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then refuses on the unset GITHUB_USERNAME (exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-validate/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/handler/tarball-validate/option-spelling/option:route:entrypoint:cmru/handler/tarball-validate/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/tarball-validate/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/tarball-validate/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | handler tarball-validate --prefix demo-v --log-prefix-time-short | {"status": 1, "stderr contains": "GITHUB_USERNAME is required", "stdout contains": ""} | Presentation only; refuses on the unset GITHUB_USERNAME (exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-validate/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/handler/tarball-validate/option-spelling/option:route:entrypoint:cmru/handler/tarball-validate/--prefix/--prefix | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/tarball-validate/--prefix"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/tarball-validate/--prefix", "spelling": "--prefix"}} | ACCEPT | handler tarball-validate --prefix demo-v | {"status": 1, "stderr contains": "GITHUB_USERNAME is required", "stdout contains": ""} | Refuses on the unset GITHUB_USERNAME (exit 1); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[handler/tarball-validate/option-spelling/--prefix/--prefix] | --prefix selects the release whose tarball and .sha256 are validated. |
| case:route:entrypoint:cmru/handler/wheel-build/minimum | minimum | {"members": ["option:route:entrypoint:cmru/handler/wheel-build/--log-prefix-time-short", "option:route:entrypoint:cmru/handler/wheel-build/--cwd"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/handler/wheel-build/--log-prefix-time-short"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": ["option:route:entrypoint:cmru/handler/wheel-build/--cwd"]}} | ACCEPT | handler wheel-build --cwd missing-dir | {"status": 3, "stderr contains": "CMRU_WHEEL_BUILDER_IMAGE is required", "stdout contains": ""} | Refuses on the undeclared builder image (exit 3); no container is started. | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-build/minimum] | --cwd PATH is the only required input; the builder image comes from CMRU_WHEEL_BUILDER_IMAGE. |
| case:route:entrypoint:cmru/handler/wheel-build/option-spelling/option:route:entrypoint:cmru/handler/wheel-build/--cwd/--cwd | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-build/--cwd"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-build/--cwd", "spelling": "--cwd"}} | ACCEPT | handler wheel-build --cwd missing-dir | {"status": 3, "stderr contains": "CMRU_WHEEL_BUILDER_IMAGE is required", "stdout contains": ""} | Refuses on the undeclared builder image (exit 3); the tree is not read. | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-build/option-spelling/--cwd/--cwd] | --cwd PATH identifies the project tree. |
| case:route:entrypoint:cmru/handler/wheel-build/option-spelling/option:route:entrypoint:cmru/handler/wheel-build/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-build/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-build/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | handler wheel-build --cwd missing-dir --debug-raw | {"status": 3, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then refuses on the undeclared builder image (exit 3). | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-build/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/handler/wheel-build/option-spelling/option:route:entrypoint:cmru/handler/wheel-build/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-build/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-build/--dry-run", "spelling": "--dry-run"}} | ACCEPT | handler wheel-build --cwd missing-dir --dry-run | {"status": 0, "stderr contains": "", "stdout contains": "[DRY RUN] Would run cmru handler wheel-build"} | Prints the inputs only; no container is started. | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-build/option-spelling/--dry-run/--dry-run] | --dry-run validates and displays the inputs without running the build. |
| case:route:entrypoint:cmru/handler/wheel-build/option-spelling/option:route:entrypoint:cmru/handler/wheel-build/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-build/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-build/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | handler wheel-build --cwd missing-dir --log-prefix-time-short | {"status": 3, "stderr contains": "CMRU_WHEEL_BUILDER_IMAGE is required", "stdout contains": ""} | Presentation only; refuses on the undeclared builder image (exit 3). | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-build/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/handler/wheel-publish/minimum | minimum | {"members": ["option:route:entrypoint:cmru/handler/wheel-publish/--log-prefix-time-short", "option:route:entrypoint:cmru/handler/wheel-publish/--prefix", "option:route:entrypoint:cmru/handler/wheel-publish/--cwd", "option:route:entrypoint:cmru/handler/wheel-publish/--extra-asset"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/handler/wheel-publish/--log-prefix-time-short", "option:route:entrypoint:cmru/handler/wheel-publish/--extra-asset"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": ["option:route:entrypoint:cmru/handler/wheel-publish/--prefix", "option:route:entrypoint:cmru/handler/wheel-publish/--cwd"]}} | ACCEPT | handler wheel-publish --prefix demo-v --cwd missing-dir | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1) before any network call. | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-publish/minimum] | --prefix and --cwd select the release namespace and source tree. |
| case:route:entrypoint:cmru/handler/wheel-publish/option-spelling/option:route:entrypoint:cmru/handler/wheel-publish/--cwd/--cwd | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-publish/--cwd"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-publish/--cwd", "spelling": "--cwd"}} | ACCEPT | handler wheel-publish --prefix demo-v --cwd missing-dir | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1); the tree is not read. | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-publish/option-spelling/--cwd/--cwd] | --cwd PATH is the source tree. |
| case:route:entrypoint:cmru/handler/wheel-publish/option-spelling/option:route:entrypoint:cmru/handler/wheel-publish/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-publish/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-publish/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | handler wheel-publish --prefix demo-v --cwd missing-dir --debug-raw | {"status": 1, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then refuses on the missing credential (exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-publish/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/handler/wheel-publish/option-spelling/option:route:entrypoint:cmru/handler/wheel-publish/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-publish/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-publish/--dry-run", "spelling": "--dry-run"}} | ACCEPT | handler wheel-publish --prefix demo-v --cwd missing-dir --dry-run | {"status": 0, "stderr contains": "", "stdout contains": "[DRY RUN] Would run cmru handler wheel-publish"} | Prints the inputs only; nothing is published and no credential is needed. | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-publish/option-spelling/--dry-run/--dry-run] | --dry-run shows the full accepted inputs and performs no publication. |
| case:route:entrypoint:cmru/handler/wheel-publish/option-spelling/option:route:entrypoint:cmru/handler/wheel-publish/--extra-asset/--extra-asset | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-publish/--extra-asset"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-publish/--extra-asset", "spelling": "--extra-asset"}} | ACCEPT | handler wheel-publish --prefix demo-v --cwd missing-dir --extra-asset extra.txt | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1); the asset is not read. | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-publish/option-spelling/--extra-asset/--extra-asset] | Repeatable --extra-asset PATH adds uploads beyond the glob selection. |
| case:route:entrypoint:cmru/handler/wheel-publish/option-spelling/option:route:entrypoint:cmru/handler/wheel-publish/--glob/--glob | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-publish/--glob"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-publish/--glob", "spelling": "--glob"}} | ACCEPT | handler wheel-publish --prefix demo-v --cwd missing-dir --glob '*.whl' | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1); no glob is evaluated. | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-publish/option-spelling/--glob/--glob] | --glob GLOB overrides the prefix-derived asset selector. |
| case:route:entrypoint:cmru/handler/wheel-publish/option-spelling/option:route:entrypoint:cmru/handler/wheel-publish/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-publish/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-publish/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | handler wheel-publish --prefix demo-v --cwd missing-dir --log-prefix-time-short | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Presentation only; refuses on the missing credential (exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-publish/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/handler/wheel-publish/option-spelling/option:route:entrypoint:cmru/handler/wheel-publish/--notes-env/--notes-env | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-publish/--notes-env"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-publish/--notes-env", "spelling": "--notes-env"}} | ACCEPT | handler wheel-publish --prefix demo-v --cwd missing-dir --notes-env NOTES | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1) before the variable is read. | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-publish/option-spelling/--notes-env/--notes-env] | --notes-env NAME names the environment variable holding release notes. |
| case:route:entrypoint:cmru/handler/wheel-publish/option-spelling/option:route:entrypoint:cmru/handler/wheel-publish/--prefix/--prefix | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-publish/--prefix"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-publish/--prefix", "spelling": "--prefix"}} | ACCEPT | handler wheel-publish --prefix demo-v --cwd missing-dir | {"status": 1, "stderr contains": "GITHUB_PUSH_PAT is required", "stdout contains": ""} | Refuses on the missing credential (exit 1) before any network call. | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-publish/option-spelling/--prefix/--prefix] | --prefix selects the release namespace. |
| case:route:entrypoint:cmru/handler/wheel-validate/minimum | minimum | {"members": ["option:route:entrypoint:cmru/handler/wheel-validate/--log-prefix-time-short", "option:route:entrypoint:cmru/handler/wheel-validate/--prefix"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/handler/wheel-validate/--log-prefix-time-short"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": ["option:route:entrypoint:cmru/handler/wheel-validate/--prefix"]}} | ACCEPT | handler wheel-validate --prefix demo-v | {"status": 1, "stderr contains": "GITHUB_USERNAME is required", "stdout contains": ""} | Refuses on the unset GITHUB_USERNAME (exit 1) before any network call. | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-validate/minimum] | --prefix selects the latest release to validate; read-only, so no --dry-run is offered. |
| case:route:entrypoint:cmru/handler/wheel-validate/option-spelling/option:route:entrypoint:cmru/handler/wheel-validate/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-validate/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-validate/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | handler wheel-validate --prefix demo-v --debug-raw | {"status": 1, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then refuses on the unset GITHUB_USERNAME (exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-validate/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/handler/wheel-validate/option-spelling/option:route:entrypoint:cmru/handler/wheel-validate/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-validate/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-validate/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | handler wheel-validate --prefix demo-v --log-prefix-time-short | {"status": 1, "stderr contains": "GITHUB_USERNAME is required", "stdout contains": ""} | Presentation only; refuses on the unset GITHUB_USERNAME (exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-validate/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/handler/wheel-validate/option-spelling/option:route:entrypoint:cmru/handler/wheel-validate/--prefix/--prefix | option-spelling | {"members": ["option:route:entrypoint:cmru/handler/wheel-validate/--prefix"], "shape": {"option_id": "option:route:entrypoint:cmru/handler/wheel-validate/--prefix", "spelling": "--prefix"}} | ACCEPT | handler wheel-validate --prefix demo-v | {"status": 1, "stderr contains": "GITHUB_USERNAME is required", "stdout contains": ""} | Refuses on the unset GITHUB_USERNAME (exit 1); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[handler/wheel-validate/option-spelling/--prefix/--prefix] | --prefix selects the latest release to validate. |
| case:route:entrypoint:cmru/init/minimum | minimum | {"members": ["option:route:entrypoint:cmru/init/--log-prefix-time-short"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/init/--log-prefix-time-short"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | init | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | Refuses at the first prompt (exit 2); no file is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/minimum] | Nothing is required: every open fact is asked through the library prompt driver, which refuses a non-terminal with exit 2; contracts are validated before anything is written. |
| case:route:entrypoint:cmru/init/option-choice/option:route:entrypoint:cmru/init/--kind/2d0edc2f06 | option-choice | {"members": ["option:route:entrypoint:cmru/init/--kind"], "shape": {"choice": "python", "option_id": "option:route:entrypoint:cmru/init/--kind"}} | ACCEPT | init --kind python | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The choice parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-choice/--kind/2d0edc2f06] | --kind python is one of the two closed project kinds (python, generic). |
| case:route:entrypoint:cmru/init/option-choice/option:route:entrypoint:cmru/init/--kind/b69715f4ba | option-choice | {"members": ["option:route:entrypoint:cmru/init/--kind"], "shape": {"choice": "generic", "option_id": "option:route:entrypoint:cmru/init/--kind"}} | ACCEPT | init --kind generic | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The choice parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-choice/--kind/b69715f4ba] | --kind generic is the other closed project kind. |
| case:route:entrypoint:cmru/init/option-choice/option:route:entrypoint:cmru/init/--layout/0fff5f85e4 | option-choice | {"members": ["option:route:entrypoint:cmru/init/--layout"], "shape": {"choice": "single", "option_id": "option:route:entrypoint:cmru/init/--layout"}} | ACCEPT | init --layout single | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The choice parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-choice/--layout/0fff5f85e4] | --layout single adopts one project; the per-project options apply. |
| case:route:entrypoint:cmru/init/option-choice/option:route:entrypoint:cmru/init/--layout/cd5d74637e | option-choice | {"members": ["option:route:entrypoint:cmru/init/--layout"], "shape": {"choice": "monorepo", "option_id": "option:route:entrypoint:cmru/init/--layout"}} | ACCEPT | init --layout monorepo | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The choice parses; per-project questions need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-choice/--layout/cd5d74637e] | --layout monorepo always asks per project; numeric 1/2 spellings were removed. |
| case:route:entrypoint:cmru/init/option-choice/option:route:entrypoint:cmru/init/--owner-type/3190d261d1 | option-choice | {"members": ["option:route:entrypoint:cmru/init/--owner-type"], "shape": {"choice": "user", "option_id": "option:route:entrypoint:cmru/init/--owner-type"}} | ACCEPT | init --owner-type user | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The choice parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-choice/--owner-type/3190d261d1] | --owner-type user is one of the two closed owner types. |
| case:route:entrypoint:cmru/init/option-choice/option:route:entrypoint:cmru/init/--owner-type/dc77373456 | option-choice | {"members": ["option:route:entrypoint:cmru/init/--owner-type"], "shape": {"choice": "org", "option_id": "option:route:entrypoint:cmru/init/--owner-type"}} | ACCEPT | init --owner-type org | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The choice parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-choice/--owner-type/dc77373456] | --owner-type org is the other closed owner type. |
| case:route:entrypoint:cmru/init/option-choice/option:route:entrypoint:cmru/init/--release-tags/04a0645267 | option-choice | {"members": ["option:route:entrypoint:cmru/init/--release-tags"], "shape": {"choice": "no", "option_id": "option:route:entrypoint:cmru/init/--release-tags"}} | ACCEPT | init --release-tags no | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The choice parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-choice/--release-tags/04a0645267] | --release-tags no declares a project without release tags. |
| case:route:entrypoint:cmru/init/option-choice/option:route:entrypoint:cmru/init/--release-tags/6c76a9331e | option-choice | {"members": ["option:route:entrypoint:cmru/init/--release-tags"], "shape": {"choice": "yes", "option_id": "option:route:entrypoint:cmru/init/--release-tags"}} | ACCEPT | init --release-tags yes | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The choice parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-choice/--release-tags/6c76a9331e] | --release-tags yes declares a project that releases through tags. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--artifacts/--artifacts | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--artifacts"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--artifacts", "spelling": "--artifacts"}} | ACCEPT | init --artifacts wheel | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The value parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--artifacts/--artifacts] | --artifacts LIST supplies the single-layout artifact list. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--build-command/--build-command | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--build-command"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--build-command", "spelling": "--build-command"}} | ACCEPT | init --build-command true | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The value parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--build-command/--build-command] | --build-command COMMAND supplies the single-layout build step. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | init --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then refuses at the first prompt (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--description/--description | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--description"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--description", "spelling": "--description"}} | ACCEPT | init --description demo | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The value parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--description/--description] | --description TEXT supplies the single-layout project description. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--dry-run", "spelling": "--dry-run"}} | ACCEPT | init --dry-run | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | Refuses at the first prompt (exit 2); with all facts it would write nothing. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--dry-run/--dry-run] | --dry-run renders and validates the contracts without writing. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--folder/--folder | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--folder"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--folder", "spelling": "--folder"}} | ACCEPT | init --folder pkg | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The value parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--folder/--folder] | --folder PATH supplies the single-layout project folder. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--id/--id | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--id"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--id", "spelling": "--id"}} | ACCEPT | init --id demo | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The value parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--id/--id] | --id ID supplies the single-layout project id (the per-project options deliberately avoid the removed --project spelling). |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--kind/--kind | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--kind"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--kind", "spelling": "--kind"}} | ACCEPT | init --kind python | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The value parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--kind/--kind] | --kind KIND is python or generic. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--layout/--layout | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--layout"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--layout", "spelling": "--layout"}} | ACCEPT | init --layout single | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The value parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--layout/--layout] | --layout is exactly single or monorepo. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | init --log-prefix-time-short | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | Presentation only; refuses at the first prompt (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--owner-type/--owner-type | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--owner-type"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--owner-type", "spelling": "--owner-type"}} | ACCEPT | init --owner-type user | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The value parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--owner-type/--owner-type] | --owner-type TYPE is user or org; otherwise read from the Git origin. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--owner/--owner | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--owner"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--owner", "spelling": "--owner"}} | ACCEPT | init --owner owner | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The value parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--owner/--owner] | --owner OWNER supplies the repository owner otherwise read from the Git origin. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--publish-command/--publish-command | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--publish-command"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--publish-command", "spelling": "--publish-command"}} | ACCEPT | init --publish-command true | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The value parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--publish-command/--publish-command] | --publish-command COMMAND supplies the single-layout publish step. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--release-tags/--release-tags | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--release-tags"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--release-tags", "spelling": "--release-tags"}} | ACCEPT | init --release-tags yes | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The value parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--release-tags/--release-tags] | --release-tags is exactly yes or no. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--repo/--repo | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--repo"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--repo", "spelling": "--repo"}} | ACCEPT | init --repo owner/repo | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | The value parses; the remaining open facts need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--repo/--repo] | --repo REPO supplies the repository name otherwise read from the Git origin. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--root/--root | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--root"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--root", "spelling": "--root"}} | REFUSE | init --root missing-dir | {"status": 2, "stderr contains": "adoption path is not an existing directory", "stdout contains": ""} | Refuses before any prompt (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--root/--root] | --root PATH selects the adoption directory and must already exist. |
| case:route:entrypoint:cmru/init/option-spelling/option:route:entrypoint:cmru/init/--yes/--yes | option-spelling | {"members": ["option:route:entrypoint:cmru/init/--yes"], "shape": {"option_id": "option:route:entrypoint:cmru/init/--yes", "spelling": "--yes"}} | ACCEPT | init --yes | {"status": 2, "stderr contains": "interactive prompts require both stdin and stdout to be terminals", "stdout contains": ""} | Open facts still need a terminal (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[init/option-spelling/--yes/--yes] | Library --yes accepts the write confirmation; it does not answer open fact questions. |
| case:route:entrypoint:cmru/publish/argument-shape/argument:route:entrypoint:cmru/publish/target | argument-shape | {"members": ["argument:route:entrypoint:cmru/publish/target"], "shape": {"argument_id": "argument:route:entrypoint:cmru/publish/target"}} | ACCEPT | publish --build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa demo | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is published. | tests/test_cli_review_cases.py::test_reviewed_case[publish/argument-shape/target] | The optional target selects projects, each of which must declare a push step; --build-output needs exactly one selected project. |
| case:route:entrypoint:cmru/publish/exclusive-conflict/publish-source/option:route:entrypoint:cmru/publish/--build-output/option:route:entrypoint:cmru/publish/--from-checkout | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/publish/--build-output", "option:route:entrypoint:cmru/publish/--from-checkout"], "shape": {"group_id": "publish-source", "options": ["option:route:entrypoint:cmru/publish/--build-output", "option:route:entrypoint:cmru/publish/--from-checkout"]}} | REFUSE | publish --build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --from-checkout | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing is published. | tests/test_cli_review_cases.py::test_reviewed_case[publish/exclusive-conflict/publish-source/--build-output/--from-checkout] | Exactly one publication source: retained build output or the caller's checkout, never both. |
| case:route:entrypoint:cmru/publish/exclusive-member/publish-source/option:route:entrypoint:cmru/publish/--build-output | exclusive-member | {"members": ["option:route:entrypoint:cmru/publish/--build-output"], "shape": {"group_id": "publish-source", "required": true, "selected_option": "option:route:entrypoint:cmru/publish/--build-output"}} | ACCEPT | publish --build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no record is read and nothing is published. | tests/test_cli_review_cases.py::test_reviewed_case[publish/exclusive-member/publish-source/--build-output] | --build-output ID accepts the exact UTC timestamp plus 40-character commit printed by `cmru build` and publishes that retained record without rebuilding; records with source-tree changes are refused. |
| case:route:entrypoint:cmru/publish/exclusive-member/publish-source/option:route:entrypoint:cmru/publish/--from-checkout | exclusive-member | {"members": ["option:route:entrypoint:cmru/publish/--from-checkout"], "shape": {"group_id": "publish-source", "required": true, "selected_option": "option:route:entrypoint:cmru/publish/--from-checkout"}} | ACCEPT | publish --from-checkout | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is published. | tests/test_cli_review_cases.py::test_reviewed_case[publish/exclusive-member/publish-source/--from-checkout] | --from-checkout explicitly publishes whatever the caller's checkout holds (not isolated). |
| case:route:entrypoint:cmru/publish/minimum | minimum | {"members": ["option:route:entrypoint:cmru/publish/--log-prefix-time-short", "option:route:entrypoint:cmru/publish/--from-checkout", "option:route:entrypoint:cmru/publish/--show-run-details", "option:route:entrypoint:cmru/publish/--log-append", "option:route:entrypoint:cmru/publish/--build-output", "option:route:entrypoint:cmru/publish/--from-checkout"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/publish/--log-prefix-time-short", "option:route:entrypoint:cmru/publish/--from-checkout", "option:route:entrypoint:cmru/publish/--show-run-details", "option:route:entrypoint:cmru/publish/--log-append"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {"publish-source": ["option:route:entrypoint:cmru/publish/--build-output", "option:route:entrypoint:cmru/publish/--from-checkout"]}, "required_options": []}} | ACCEPT | publish --build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is published. | tests/test_cli_review_cases.py::test_reviewed_case[publish/minimum] | Exactly one source is required (--build-output or --from-checkout); a missing push step fails as a usage/configuration error before credentials or external actions. |
| case:route:entrypoint:cmru/publish/option-spelling/option:route:entrypoint:cmru/publish/--build-output/--build-output | option-spelling | {"members": ["option:route:entrypoint:cmru/publish/--build-output"], "shape": {"option_id": "option:route:entrypoint:cmru/publish/--build-output", "spelling": "--build-output"}} | ACCEPT | publish --build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is published. | tests/test_cli_review_cases.py::test_reviewed_case[publish/option-spelling/--build-output/--build-output] | Exact spelling of the retained-record source; one non-empty ID. |
| case:route:entrypoint:cmru/publish/option-spelling/option:route:entrypoint:cmru/publish/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/publish/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/publish/--config", "spelling": "--config"}} | REFUSE | publish --build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2); nothing is published. | tests/test_cli_review_cases.py::test_reviewed_case[publish/option-spelling/--config/--config] | --config selects the project contract; only the two canonical names are accepted. |
| case:route:entrypoint:cmru/publish/option-spelling/option:route:entrypoint:cmru/publish/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/publish/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/publish/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | publish --build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[publish/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/publish/option-spelling/option:route:entrypoint:cmru/publish/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/publish/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/publish/--dry-run", "spelling": "--dry-run"}} | ACCEPT | publish --build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --dry-run | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); with a config the preview publishes nothing. | tests/test_cli_review_cases.py::test_reviewed_case[publish/option-spelling/--dry-run/--dry-run] | --dry-run displays the record, digests and publisher commands without credentials or execution, and without querying remote tags. |
| case:route:entrypoint:cmru/publish/option-spelling/option:route:entrypoint:cmru/publish/--from-checkout/--from-checkout | option-spelling | {"members": ["option:route:entrypoint:cmru/publish/--from-checkout"], "shape": {"option_id": "option:route:entrypoint:cmru/publish/--from-checkout", "spelling": "--from-checkout"}} | ACCEPT | publish --from-checkout | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is published. | tests/test_cli_review_cases.py::test_reviewed_case[publish/option-spelling/--from-checkout/--from-checkout] | Exact spelling of the explicit checkout source. |
| case:route:entrypoint:cmru/publish/option-spelling/option:route:entrypoint:cmru/publish/--log-append/--log-append | option-spelling | {"members": ["option:route:entrypoint:cmru/publish/--log-append"], "shape": {"option_id": "option:route:entrypoint:cmru/publish/--log-append", "spelling": "--log-append"}} | ACCEPT | publish --build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --log-append | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no log is opened. | tests/test_cli_review_cases.py::test_reviewed_case[publish/option-spelling/--log-append/--log-append] | --log-append retains prior step logs. |
| case:route:entrypoint:cmru/publish/option-spelling/option:route:entrypoint:cmru/publish/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/publish/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/publish/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | publish --build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[publish/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/publish/option-spelling/option:route:entrypoint:cmru/publish/--show-run-details/--show-run-details | option-spelling | {"members": ["option:route:entrypoint:cmru/publish/--show-run-details"], "shape": {"option_id": "option:route:entrypoint:cmru/publish/--show-run-details", "spelling": "--show-run-details"}} | ACCEPT | publish --build-output 20260101T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --show-run-details | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing streams. | tests/test_cli_review_cases.py::test_reviewed_case[publish/option-spelling/--show-run-details/--show-run-details] | --show-run-details streams publisher output. |
| case:route:entrypoint:cmru/release/argument-shape/argument:route:entrypoint:cmru/release/target | argument-shape | {"members": ["argument:route:entrypoint:cmru/release/target"], "shape": {"argument_id": "argument:route:entrypoint:cmru/release/target"}} | ACCEPT | release demo | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no candidate is created. | tests/test_cli_review_cases.py::test_reviewed_case[release/argument-shape/target] | The optional target selects projects; --set-version additionally requires exactly one selected project. |
| case:route:entrypoint:cmru/release/exclusive-conflict/version-override/option:route:entrypoint:cmru/release/--major/option:route:entrypoint:cmru/release/--minor | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/release/--major", "option:route:entrypoint:cmru/release/--minor"], "shape": {"group_id": "version-override", "options": ["option:route:entrypoint:cmru/release/--major", "option:route:entrypoint:cmru/release/--minor"]}} | REFUSE | release --major --minor | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing changes. | tests/test_cli_review_cases.py::test_reviewed_case[release/exclusive-conflict/version-override/--major/--minor] | --minor, --major and --set-version are mutually exclusive version overrides. |
| case:route:entrypoint:cmru/release/exclusive-conflict/version-override/option:route:entrypoint:cmru/release/--major/option:route:entrypoint:cmru/release/--set-version | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/release/--major", "option:route:entrypoint:cmru/release/--set-version"], "shape": {"group_id": "version-override", "options": ["option:route:entrypoint:cmru/release/--major", "option:route:entrypoint:cmru/release/--set-version"]}} | REFUSE | release --major --set-version 1.2.3 | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing changes. | tests/test_cli_review_cases.py::test_reviewed_case[release/exclusive-conflict/version-override/--major/--set-version] | --minor, --major and --set-version are mutually exclusive version overrides. |
| case:route:entrypoint:cmru/release/exclusive-conflict/version-override/option:route:entrypoint:cmru/release/--minor/option:route:entrypoint:cmru/release/--set-version | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/release/--minor", "option:route:entrypoint:cmru/release/--set-version"], "shape": {"group_id": "version-override", "options": ["option:route:entrypoint:cmru/release/--minor", "option:route:entrypoint:cmru/release/--set-version"]}} | REFUSE | release --minor --set-version 1.2.3 | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing changes. | tests/test_cli_review_cases.py::test_reviewed_case[release/exclusive-conflict/version-override/--minor/--set-version] | --minor, --major and --set-version are mutually exclusive version overrides. |
| case:route:entrypoint:cmru/release/exclusive-member/version-override/option:route:entrypoint:cmru/release/--major | exclusive-member | {"members": ["option:route:entrypoint:cmru/release/--major"], "shape": {"group_id": "version-override", "required": false, "selected_option": "option:route:entrypoint:cmru/release/--major"}} | ACCEPT | release --major | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no candidate is created. | tests/test_cli_review_cases.py::test_reviewed_case[release/exclusive-member/version-override/--major] | --major bumps major versions; rejected for external-version or no-tag projects. |
| case:route:entrypoint:cmru/release/exclusive-member/version-override/option:route:entrypoint:cmru/release/--minor | exclusive-member | {"members": ["option:route:entrypoint:cmru/release/--minor"], "shape": {"group_id": "version-override", "required": false, "selected_option": "option:route:entrypoint:cmru/release/--minor"}} | ACCEPT | release --minor | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no candidate is created. | tests/test_cli_review_cases.py::test_reviewed_case[release/exclusive-member/version-override/--minor] | --minor bumps minor versions; rejected for external-version or no-tag projects. |
| case:route:entrypoint:cmru/release/exclusive-member/version-override/option:route:entrypoint:cmru/release/--set-version | exclusive-member | {"members": ["option:route:entrypoint:cmru/release/--set-version"], "shape": {"group_id": "version-override", "required": false, "selected_option": "option:route:entrypoint:cmru/release/--set-version"}} | ACCEPT | release --set-version 1.2.3 | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no candidate is created. | tests/test_cli_review_cases.py::test_reviewed_case[release/exclusive-member/version-override/--set-version] | --set-version VER sets an explicit version for exactly one selected project. |
| case:route:entrypoint:cmru/release/minimum | minimum | {"members": ["option:route:entrypoint:cmru/release/--log-prefix-time-short", "option:route:entrypoint:cmru/release/--minor", "option:route:entrypoint:cmru/release/--major", "option:route:entrypoint:cmru/release/--no-build", "option:route:entrypoint:cmru/release/--allow-uncommitted", "option:route:entrypoint:cmru/release/--allow-tag-ahead-of-head", "option:route:entrypoint:cmru/release/--allow-stale-tool-deps", "option:route:entrypoint:cmru/release/--show-run-details", "option:route:entrypoint:cmru/release/--log-append"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/release/--log-prefix-time-short", "option:route:entrypoint:cmru/release/--minor", "option:route:entrypoint:cmru/release/--major", "option:route:entrypoint:cmru/release/--no-build", "option:route:entrypoint:cmru/release/--allow-uncommitted", "option:route:entrypoint:cmru/release/--allow-tag-ahead-of-head", "option:route:entrypoint:cmru/release/--allow-stale-tool-deps", "option:route:entrypoint:cmru/release/--show-run-details", "option:route:entrypoint:cmru/release/--log-append"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | release | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no candidate, tag or push exists. Plan refusals after config use exit 4, failures after start exit 1. | tests/test_cli_review_cases.py::test_reviewed_case[release/minimum] | Nothing is required: a bare `release` is the atomic source-first release of the current project, with the version derived from the changelog/commits. |
| case:route:entrypoint:cmru/release/option-choice/option:route:entrypoint:cmru/release/--discard/6f1976ed2d | option-choice | {"members": ["option:route:entrypoint:cmru/release/--discard"], "shape": {"choice": "artifacts", "option_id": "option:route:entrypoint:cmru/release/--discard"}} | ACCEPT | release --discard artifacts | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is retained or discarded. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-choice/--discard/6f1976ed2d] | --discard artifacts drops build artifacts from a successful release's retention. |
| case:route:entrypoint:cmru/release/option-choice/option:route:entrypoint:cmru/release/--discard/a05264829b | option-choice | {"members": ["option:route:entrypoint:cmru/release/--discard"], "shape": {"choice": "logs", "option_id": "option:route:entrypoint:cmru/release/--discard"}} | ACCEPT | release --discard logs | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is retained or discarded. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-choice/--discard/a05264829b] | --discard logs drops logs from a successful release's retention. |
| case:route:entrypoint:cmru/release/option-choice/option:route:entrypoint:cmru/release/--discard/edecec53ab | option-choice | {"members": ["option:route:entrypoint:cmru/release/--discard"], "shape": {"choice": "evidence", "option_id": "option:route:entrypoint:cmru/release/--discard"}} | ACCEPT | release --discard evidence | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is retained or discarded. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-choice/--discard/edecec53ab] | --discard evidence drops evidence from a successful release's retention. |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--ahead-check-ref/--ahead-check-ref | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--ahead-check-ref"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--ahead-check-ref", "spelling": "--ahead-check-ref"}} | ACCEPT | release --ahead-check-ref main | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no guard runs. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--ahead-check-ref/--ahead-check-ref] | --ahead-check-ref REF sets the ref the local-ahead guard compares against (the snapshot itself is always origin/main); it replaces the old --ref spelling. |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--allow-stale-tool-deps/--allow-stale-tool-deps | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--allow-stale-tool-deps"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--allow-stale-tool-deps", "spelling": "--allow-stale-tool-deps"}} | ACCEPT | release --allow-stale-tool-deps | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no verification runs. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--allow-stale-tool-deps/--allow-stale-tool-deps] | --allow-stale-tool-deps relaxes tool-dependency freshness only. |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--allow-tag-ahead-of-head/--allow-tag-ahead-of-head | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--allow-tag-ahead-of-head"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--allow-tag-ahead-of-head", "spelling": "--allow-tag-ahead-of-head"}} | ACCEPT | release --allow-tag-ahead-of-head | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no baseline check runs. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--allow-tag-ahead-of-head/--allow-tag-ahead-of-head] | --allow-tag-ahead-of-head allows only the strictly-ahead baseline case (the removed --allow-tag-at-head spelling has no compatibility window). |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--allow-uncommitted/--allow-uncommitted | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--allow-uncommitted"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--allow-uncommitted", "spelling": "--allow-uncommitted"}} | ACCEPT | release --allow-uncommitted | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no snapshot is taken. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--allow-uncommitted/--allow-uncommitted] | --allow-uncommitted permits caller edits to be omitted from the origin/main snapshot instead of blocking the release (exit 4 otherwise). |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--config", "spelling": "--config"}} | REFUSE | release --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2); nothing changes. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--config/--config] | --config selects the project contract; only the two canonical names are accepted. |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | release --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--discard/--discard | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--discard"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--discard", "spelling": "--discard"}} | ACCEPT | release --discard logs | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is retained or discarded. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--discard/--discard] | Repeatable --discard {logs,artifacts,evidence} changes successful retention. |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--dry-run", "spelling": "--dry-run"}} | ACCEPT | release --dry-run | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no candidate is created. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--dry-run/--dry-run] | --dry-run runs external-version preparation only inside a disposable managed candidate to derive the plan; it does not gate, tag, build, publish or promote. |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--log-append/--log-append | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--log-append"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--log-append", "spelling": "--log-append"}} | ACCEPT | release --log-append | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no log is opened. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--log-append/--log-append] | --log-append retains prior step logs. |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | release --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--major/--major | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--major"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--major", "spelling": "--major"}} | ACCEPT | release --major | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no candidate is created. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--major/--major] | Exact spelling of the major bump. |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--minor/--minor | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--minor"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--minor", "spelling": "--minor"}} | ACCEPT | release --minor | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no candidate is created. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--minor/--minor] | Exact spelling of the minor bump. |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--no-build/--no-build | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--no-build"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--no-build", "spelling": "--no-build"}} | ACCEPT | release --no-build | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is tagged or pushed. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--no-build/--no-build] | --no-build intentionally stops after tag and push (no build or publish). |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--ref/--ref | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--ref"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--ref", "spelling": "--ref"}} | ACCEPT | release --ref main | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing runs. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--ref/--ref] | --ref REF is the deprecated spelling of --ahead-check-ref, accepted with a warning for one release. |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--resume/--resume | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--resume"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--resume", "spelling": "--resume"}} | ACCEPT | release --resume missing-worktree | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no worktree is touched. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--resume/--resume] | --resume WORKTREE resumes a retained pre-tag candidate using its recorded scope; an explicit target must match; it is not post-tag publication recovery (KI-06). |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--set-version/--set-version | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--set-version"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--set-version", "spelling": "--set-version"}} | ACCEPT | release --set-version 1.2.3 | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no candidate is created. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--set-version/--set-version] | Exact spelling of the explicit-version override. |
| case:route:entrypoint:cmru/release/option-spelling/option:route:entrypoint:cmru/release/--show-run-details/--show-run-details | option-spelling | {"members": ["option:route:entrypoint:cmru/release/--show-run-details"], "shape": {"option_id": "option:route:entrypoint:cmru/release/--show-run-details", "spelling": "--show-run-details"}} | ACCEPT | release --show-run-details | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing streams. | tests/test_cli_review_cases.py::test_reviewed_case[release/option-spelling/--show-run-details/--show-run-details] | --show-run-details streams subprocess output. |
| case:route:entrypoint:cmru/resolve/argument-shape/argument:route:entrypoint:cmru/resolve/target | argument-shape | {"members": ["argument:route:entrypoint:cmru/resolve/target"], "shape": {"argument_id": "argument:route:entrypoint:cmru/resolve/target"}} | ACCEPT | resolve demo | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is read from the network. | tests/test_cli_review_cases.py::test_reviewed_case[resolve/argument-shape/target] | The optional target selects a project; the JSON shape follows the selector syntax (one name prints an object, `all` or a list a keyed map). A target conflicts with config-free --repo mode. |
| case:route:entrypoint:cmru/resolve/constraint-conflict/1 | constraint-conflict | {"members": ["option:route:entrypoint:cmru/resolve/--repo", "option:route:entrypoint:cmru/resolve/--config"], "shape": {"kind": "conflicts", "options": ["--repo", "--config"], "reason": "config-free mode reads no configuration"}} | REFUSE | resolve --repo owner/repo --config missing.toml | {"status": 2, "stderr contains": "cannot be used together", "stdout contains": ""} | Refused as a usage error (exit 2); no network request. | tests/test_cli_review_cases.py::test_reviewed_case[resolve/constraint-conflict/1] | Config-free mode (--repo/--prefix) reads no configuration, so combining it with --config is refused. |
| case:route:entrypoint:cmru/resolve/constraint-requires/2 | constraint-requires | {"members": ["option:route:entrypoint:cmru/resolve/--repo", "option:route:entrypoint:cmru/resolve/--prefix"], "shape": {"any_of": ["--prefix"], "kind": "requires", "option": "--repo", "reason": "a repository needs the tag prefix to resolve"}} | REFUSE | resolve --repo owner/repo | {"status": 2, "stderr contains": "--repo requires --prefix", "stdout contains": ""} | Refused as a usage error (exit 2); no network request. | tests/test_cli_review_cases.py::test_reviewed_case[resolve/constraint-requires/2] | A repository needs the tag prefix to resolve: --repo requires --prefix. |
| case:route:entrypoint:cmru/resolve/constraint-requires/3 | constraint-requires | {"members": ["option:route:entrypoint:cmru/resolve/--prefix", "option:route:entrypoint:cmru/resolve/--repo"], "shape": {"any_of": ["--repo"], "kind": "requires", "option": "--prefix", "reason": "a prefix needs the repository to resolve in"}} | REFUSE | resolve --prefix demo-v | {"status": 2, "stderr contains": "--prefix requires --repo", "stdout contains": ""} | Refused as a usage error (exit 2); no network request. | tests/test_cli_review_cases.py::test_reviewed_case[resolve/constraint-requires/3] | A prefix needs the repository to resolve in: --prefix requires --repo. |
| case:route:entrypoint:cmru/resolve/minimum | minimum | {"members": ["option:route:entrypoint:cmru/resolve/--log-prefix-time-short", "option:route:entrypoint:cmru/resolve/--format"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/resolve/--log-prefix-time-short", "option:route:entrypoint:cmru/resolve/--format"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | resolve | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no network request. | tests/test_cli_review_cases.py::test_reviewed_case[resolve/minimum] | Nothing is required: it reads the latest published artifact facts of the current project, read-only (no --dry-run). |
| case:route:entrypoint:cmru/resolve/option-choice/option:route:entrypoint:cmru/resolve/--format/4147dd2e1f | option-choice | {"members": ["option:route:entrypoint:cmru/resolve/--format"], "shape": {"choice": "env", "option_id": "option:route:entrypoint:cmru/resolve/--format"}} | ACCEPT | resolve --format env | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing printed. | tests/test_cli_review_cases.py::test_reviewed_case[resolve/option-choice/--format/4147dd2e1f] | --format env prints shell-consumable assignments. |
| case:route:entrypoint:cmru/resolve/option-choice/option:route:entrypoint:cmru/resolve/--format/4e23b39204 | option-choice | {"members": ["option:route:entrypoint:cmru/resolve/--format"], "shape": {"choice": "json", "option_id": "option:route:entrypoint:cmru/resolve/--format"}} | ACCEPT | resolve --format json | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing printed. | tests/test_cli_review_cases.py::test_reviewed_case[resolve/option-choice/--format/4e23b39204] | --format json is the default machine format. |
| case:route:entrypoint:cmru/resolve/option-choice/option:route:entrypoint:cmru/resolve/--format/a8aefc3749 | option-choice | {"members": ["option:route:entrypoint:cmru/resolve/--format"], "shape": {"choice": "url", "option_id": "option:route:entrypoint:cmru/resolve/--format"}} | ACCEPT | resolve --format url | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing printed. | tests/test_cli_review_cases.py::test_reviewed_case[resolve/option-choice/--format/a8aefc3749] | --format url prints the resolved artifact URL. |
| case:route:entrypoint:cmru/resolve/option-spelling/option:route:entrypoint:cmru/resolve/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/resolve/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/resolve/--config", "spelling": "--config"}} | REFUSE | resolve --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2); no network request. | tests/test_cli_review_cases.py::test_reviewed_case[resolve/option-spelling/--config/--config] | --config selects the project contract; only the two canonical names are accepted; it conflicts with --repo. |
| case:route:entrypoint:cmru/resolve/option-spelling/option:route:entrypoint:cmru/resolve/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/resolve/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/resolve/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | resolve --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[resolve/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/resolve/option-spelling/option:route:entrypoint:cmru/resolve/--format/--format | option-spelling | {"members": ["option:route:entrypoint:cmru/resolve/--format"], "shape": {"option_id": "option:route:entrypoint:cmru/resolve/--format", "spelling": "--format"}} | ACCEPT | resolve --format json | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing printed. | tests/test_cli_review_cases.py::test_reviewed_case[resolve/option-spelling/--format/--format] | --format is closed to json, env, url; an accepted exception to the library --json because shell consumers need env and url. |
| case:route:entrypoint:cmru/resolve/option-spelling/option:route:entrypoint:cmru/resolve/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/resolve/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/resolve/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | resolve --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[resolve/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/resolve/option-spelling/option:route:entrypoint:cmru/resolve/--prefix/--prefix | option-spelling | {"members": ["option:route:entrypoint:cmru/resolve/--prefix"], "shape": {"option_id": "option:route:entrypoint:cmru/resolve/--prefix", "spelling": "--prefix"}} | ACCEPT | resolve --repo owner/repo --prefix demo-v | {"status": 3, "stderr contains": "cannot reach owner/repo", "stdout contains": ""} | The sandbox blocks the network, so the one GitHub read fails (exit 3); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[resolve/option-spelling/--prefix/--prefix] | --prefix PREFIX (with --repo) resolves without any configuration; an unreachable registry is a missing prerequisite (exit 3, one line, no traceback; fixed in W2-PKG5). |
| case:route:entrypoint:cmru/resolve/option-spelling/option:route:entrypoint:cmru/resolve/--repo/--repo | option-spelling | {"members": ["option:route:entrypoint:cmru/resolve/--repo"], "shape": {"option_id": "option:route:entrypoint:cmru/resolve/--repo", "spelling": "--repo"}} | ACCEPT | resolve --prefix demo-v --repo owner/repo | {"status": 3, "stderr contains": "cannot reach owner/repo", "stdout contains": ""} | The sandbox blocks the network, so the one GitHub read fails (exit 3); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[resolve/option-spelling/--repo/--repo] | --repo OWNER/REPO (with --prefix) is config-free resolution; the token comes from GITHUB_PUSH_PAT/GITHUB_TOKEN and is optional for public repositories. |
| case:route:entrypoint:cmru/run/argument-shape/argument:route:entrypoint:cmru/run/target | argument-shape | {"members": ["argument:route:entrypoint:cmru/run/target"], "shape": {"argument_id": "argument:route:entrypoint:cmru/run/target"}} | ACCEPT | run demo | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no step runs. | tests/test_cli_review_cases.py::test_reviewed_case[run/argument-shape/target] | The optional target selects projects; omitted uses the invocation context's default project selection. |
| case:route:entrypoint:cmru/run/minimum | minimum | {"members": ["option:route:entrypoint:cmru/run/--log-prefix-time-short", "option:route:entrypoint:cmru/run/--show-run-details", "option:route:entrypoint:cmru/run/--log-append"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/run/--log-prefix-time-short", "option:route:entrypoint:cmru/run/--show-run-details", "option:route:entrypoint:cmru/run/--log-append"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | run | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no step runs. | tests/test_cli_review_cases.py::test_reviewed_case[run/minimum] | Nothing is required: with no --step the configured default_steps run in the caller's checkout; an empty default is a no-op. |
| case:route:entrypoint:cmru/run/option-spelling/option:route:entrypoint:cmru/run/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/run/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/run/--config", "spelling": "--config"}} | REFUSE | run --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2); no step runs. | tests/test_cli_review_cases.py::test_reviewed_case[run/option-spelling/--config/--config] | --config selects the project or orchestration file; only the two canonical names are accepted. |
| case:route:entrypoint:cmru/run/option-spelling/option:route:entrypoint:cmru/run/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/run/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/run/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | run --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[run/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/run/option-spelling/option:route:entrypoint:cmru/run/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/run/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/run/--dry-run", "spelling": "--dry-run"}} | ACCEPT | run --dry-run | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); with a config the preview starts no command. | tests/test_cli_review_cases.py::test_reviewed_case[run/option-spelling/--dry-run/--dry-run] | --dry-run resolves the same target, steps and order and prints declared cleanup, environment inputs, argv and cwd without starting project commands (dynamic env_command output is named, not run). |
| case:route:entrypoint:cmru/run/option-spelling/option:route:entrypoint:cmru/run/--log-append/--log-append | option-spelling | {"members": ["option:route:entrypoint:cmru/run/--log-append"], "shape": {"option_id": "option:route:entrypoint:cmru/run/--log-append", "spelling": "--log-append"}} | ACCEPT | run --log-append | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no log is opened. | tests/test_cli_review_cases.py::test_reviewed_case[run/option-spelling/--log-append/--log-append] | --log-append retains prior step logs. |
| case:route:entrypoint:cmru/run/option-spelling/option:route:entrypoint:cmru/run/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/run/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/run/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | run --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[run/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/run/option-spelling/option:route:entrypoint:cmru/run/--show-run-details/--show-run-details | option-spelling | {"members": ["option:route:entrypoint:cmru/run/--show-run-details"], "shape": {"option_id": "option:route:entrypoint:cmru/run/--show-run-details", "spelling": "--show-run-details"}} | ACCEPT | run --show-run-details | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing streams. | tests/test_cli_review_cases.py::test_reviewed_case[run/option-spelling/--show-run-details/--show-run-details] | --show-run-details streams subprocess output. |
| case:route:entrypoint:cmru/run/option-spelling/option:route:entrypoint:cmru/run/--step/--step | option-spelling | {"members": ["option:route:entrypoint:cmru/run/--step"], "shape": {"option_id": "option:route:entrypoint:cmru/run/--step", "spelling": "--step"}} | ACCEPT | run --step build | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no step runs. | tests/test_cli_review_cases.py::test_reviewed_case[run/option-spelling/--step/--step] | Repeatable --step NAME names declared steps, run in the order given; a step a selected project does not declare is a usage error (exit 2) naming the declared steps. |
| case:route:entrypoint:cmru/skills/check/exclusive-conflict/target/option:route:entrypoint:cmru/skills/check/--dest/option:route:entrypoint:cmru/skills/check/--harness | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/skills/check/--dest", "option:route:entrypoint:cmru/skills/check/--harness"], "shape": {"group_id": "target", "options": ["option:route:entrypoint:cmru/skills/check/--dest", "option:route:entrypoint:cmru/skills/check/--harness"]}} | REFUSE | skills check --dest dest --harness claude | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing is read. | tests/test_cli_review_cases.py::test_reviewed_case[skills/check/exclusive-conflict/target/--dest/--harness] | --harness and --dest are mutually exclusive destination selectors. |
| case:route:entrypoint:cmru/skills/check/exclusive-member/target/option:route:entrypoint:cmru/skills/check/--dest | exclusive-member | {"members": ["option:route:entrypoint:cmru/skills/check/--dest"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:cmru/skills/check/--dest"}} | ACCEPT | skills check --dest dest | {"status": 1, "stderr contains": "skill(s) are not current", "stdout contains": "absent"} | Read-only; an empty destination is not current (exit 1); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[skills/check/exclusive-member/target/--dest] | --dest DIR checks exactly that directory. |
| case:route:entrypoint:cmru/skills/check/exclusive-member/target/option:route:entrypoint:cmru/skills/check/--harness | exclusive-member | {"members": ["option:route:entrypoint:cmru/skills/check/--harness"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:cmru/skills/check/--harness"}} | ACCEPT | skills check --harness claude | {"status": 1, "stderr contains": "skill(s) are not current", "stdout contains": "absent"} | Read-only under the sandbox HOME; an empty directory is not current (exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[skills/check/exclusive-member/target/--harness] | --harness claude\|agents\|all checks that harness's skills directory under HOME. |
| case:route:entrypoint:cmru/skills/check/minimum | minimum | {"members": [], "shape": {"defaulted_options": [], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | skills check | {"status": 1, "stderr contains": "skill(s) are not current", "stdout contains": "absent"} | Read-only under the sandbox HOME; nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[skills/check/minimum] | Nothing is required: it checks every harness (the default) and exits 1 unless every packaged skill is current. |
| case:route:entrypoint:cmru/skills/check/option-choice/option:route:entrypoint:cmru/skills/check/--harness/389432cbf8 | option-choice | {"members": ["option:route:entrypoint:cmru/skills/check/--harness"], "shape": {"choice": "claude", "option_id": "option:route:entrypoint:cmru/skills/check/--harness"}} | ACCEPT | skills check --harness claude | {"status": 1, "stderr contains": "skill(s) are not current", "stdout contains": "absent"} | Read-only under the sandbox HOME (exit 1 when absent). | tests/test_cli_review_cases.py::test_reviewed_case[skills/check/option-choice/--harness/389432cbf8] | --harness claude selects the Claude skills directory. |
| case:route:entrypoint:cmru/skills/check/option-choice/option:route:entrypoint:cmru/skills/check/--harness/5cf7504d97 | option-choice | {"members": ["option:route:entrypoint:cmru/skills/check/--harness"], "shape": {"choice": "all", "option_id": "option:route:entrypoint:cmru/skills/check/--harness"}} | ACCEPT | skills check --harness all | {"status": 1, "stderr contains": "skill(s) are not current", "stdout contains": "absent"} | Read-only under the sandbox HOME (exit 1 when absent). | tests/test_cli_review_cases.py::test_reviewed_case[skills/check/option-choice/--harness/5cf7504d97] | --harness all selects every harness directory (the default). |
| case:route:entrypoint:cmru/skills/check/option-choice/option:route:entrypoint:cmru/skills/check/--harness/bc42532430 | option-choice | {"members": ["option:route:entrypoint:cmru/skills/check/--harness"], "shape": {"choice": "agents", "option_id": "option:route:entrypoint:cmru/skills/check/--harness"}} | ACCEPT | skills check --harness agents | {"status": 1, "stderr contains": "skill(s) are not current", "stdout contains": "absent"} | Read-only under the sandbox HOME (exit 1 when absent). | tests/test_cli_review_cases.py::test_reviewed_case[skills/check/option-choice/--harness/bc42532430] | --harness agents selects the shared agents skills directory. |
| case:route:entrypoint:cmru/skills/check/option-spelling/option:route:entrypoint:cmru/skills/check/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/check/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/check/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | skills check --debug-raw | {"status": 1, "stderr contains": "--debug-raw is active", "stdout contains": "absent"} | Prints the warning; the check stays read-only (exit 1 when absent). | tests/test_cli_review_cases.py::test_reviewed_case[skills/check/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/skills/check/option-spelling/option:route:entrypoint:cmru/skills/check/--dest/--dest | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/check/--dest"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/check/--dest", "spelling": "--dest"}} | ACCEPT | skills check --dest dest | {"status": 1, "stderr contains": "skill(s) are not current", "stdout contains": "absent"} | Read-only; an empty destination is not current (exit 1). | tests/test_cli_review_cases.py::test_reviewed_case[skills/check/option-spelling/--dest/--dest] | Exact spelling of the explicit destination. |
| case:route:entrypoint:cmru/skills/check/option-spelling/option:route:entrypoint:cmru/skills/check/--harness/--harness | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/check/--harness"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/check/--harness", "spelling": "--harness"}} | ACCEPT | skills check --harness claude | {"status": 1, "stderr contains": "skill(s) are not current", "stdout contains": "absent"} | Read-only under the sandbox HOME (exit 1 when absent). | tests/test_cli_review_cases.py::test_reviewed_case[skills/check/option-spelling/--harness/--harness] | Exact spelling of the harness selector (claude, agents, all). |
| case:route:entrypoint:cmru/skills/check/option-spelling/option:route:entrypoint:cmru/skills/check/--json/--json | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/check/--json"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/check/--json", "spelling": "--json"}} | ACCEPT | skills check --json | {"status": 1, "stderr contains": "skill(s) are not current", "stdout contains": "\"skills\""} | Read-only under the sandbox HOME; machine output only. | tests/test_cli_review_cases.py::test_reviewed_case[skills/check/option-spelling/--json/--json] | --json emits {tool, version, skills[...]} on stdout; the exit rule is unchanged. |
| case:route:entrypoint:cmru/skills/install/exclusive-conflict/target/option:route:entrypoint:cmru/skills/install/--dest/option:route:entrypoint:cmru/skills/install/--harness | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/skills/install/--dest", "option:route:entrypoint:cmru/skills/install/--harness"], "shape": {"group_id": "target", "options": ["option:route:entrypoint:cmru/skills/install/--dest", "option:route:entrypoint:cmru/skills/install/--harness"]}} | REFUSE | skills install --dest dest --harness claude | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[skills/install/exclusive-conflict/target/--dest/--harness] | --harness and --dest are mutually exclusive destination selectors. |
| case:route:entrypoint:cmru/skills/install/exclusive-member/target/option:route:entrypoint:cmru/skills/install/--dest | exclusive-member | {"members": ["option:route:entrypoint:cmru/skills/install/--dest"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:cmru/skills/install/--dest"}} | ACCEPT | skills install --dest dest | {"status": 0, "stderr contains": "", "stdout contains": "installed cmru-cli"} | Writes the skill only under the given directory (a tmp cwd in the test). | tests/test_cli_review_cases.py::test_reviewed_case[skills/install/exclusive-member/target/--dest] | --dest DIR installs the packaged skill into exactly that directory. |
| case:route:entrypoint:cmru/skills/install/exclusive-member/target/option:route:entrypoint:cmru/skills/install/--harness | exclusive-member | {"members": ["option:route:entrypoint:cmru/skills/install/--harness"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:cmru/skills/install/--harness"}} | ACCEPT | skills install --harness claude | {"status": 0, "stderr contains": "", "stdout contains": "installed cmru-cli"} | Writes only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/install/exclusive-member/target/--harness] | --harness claude\|agents\|all installs into that harness's skills directory under HOME. |
| case:route:entrypoint:cmru/skills/install/minimum | minimum | {"members": ["option:route:entrypoint:cmru/skills/install/--overwrite-modified"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/skills/install/--overwrite-modified"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | skills install | {"status": 0, "stderr contains": "", "stdout contains": "installed cmru-cli"} | Writes only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/install/minimum] | Nothing is required: it installs the packaged skill into every harness directory (the default); a locally modified skill is never overwritten without --overwrite-modified. |
| case:route:entrypoint:cmru/skills/install/option-choice/option:route:entrypoint:cmru/skills/install/--harness/389432cbf8 | option-choice | {"members": ["option:route:entrypoint:cmru/skills/install/--harness"], "shape": {"choice": "claude", "option_id": "option:route:entrypoint:cmru/skills/install/--harness"}} | ACCEPT | skills install --harness claude | {"status": 0, "stderr contains": "", "stdout contains": "installed cmru-cli"} | Writes only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/install/option-choice/--harness/389432cbf8] | --harness claude selects the Claude skills directory. |
| case:route:entrypoint:cmru/skills/install/option-choice/option:route:entrypoint:cmru/skills/install/--harness/5cf7504d97 | option-choice | {"members": ["option:route:entrypoint:cmru/skills/install/--harness"], "shape": {"choice": "all", "option_id": "option:route:entrypoint:cmru/skills/install/--harness"}} | ACCEPT | skills install --harness all | {"status": 0, "stderr contains": "", "stdout contains": "installed cmru-cli"} | Writes only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/install/option-choice/--harness/5cf7504d97] | --harness all selects every harness directory (the default). |
| case:route:entrypoint:cmru/skills/install/option-choice/option:route:entrypoint:cmru/skills/install/--harness/bc42532430 | option-choice | {"members": ["option:route:entrypoint:cmru/skills/install/--harness"], "shape": {"choice": "agents", "option_id": "option:route:entrypoint:cmru/skills/install/--harness"}} | ACCEPT | skills install --harness agents | {"status": 0, "stderr contains": "", "stdout contains": "installed cmru-cli"} | Writes only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/install/option-choice/--harness/bc42532430] | --harness agents selects the shared agents skills directory. |
| case:route:entrypoint:cmru/skills/install/option-spelling/option:route:entrypoint:cmru/skills/install/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/install/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/install/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | skills install --debug-raw | {"status": 0, "stderr contains": "--debug-raw is active", "stdout contains": "installed cmru-cli"} | Prints the warning; installs only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/install/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/skills/install/option-spelling/option:route:entrypoint:cmru/skills/install/--dest/--dest | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/install/--dest"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/install/--dest", "spelling": "--dest"}} | ACCEPT | skills install --dest dest | {"status": 0, "stderr contains": "", "stdout contains": "installed cmru-cli"} | Writes the skill only under the given directory. | tests/test_cli_review_cases.py::test_reviewed_case[skills/install/option-spelling/--dest/--dest] | Exact spelling of the explicit destination. |
| case:route:entrypoint:cmru/skills/install/option-spelling/option:route:entrypoint:cmru/skills/install/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/install/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/install/--dry-run", "spelling": "--dry-run"}} | ACCEPT | skills install --dry-run | {"status": 0, "stderr contains": "", "stdout contains": "would install cmru-cli"} | Writes nothing. | tests/test_cli_review_cases.py::test_reviewed_case[skills/install/option-spelling/--dry-run/--dry-run] | --dry-run reports what would be installed without changing anything. |
| case:route:entrypoint:cmru/skills/install/option-spelling/option:route:entrypoint:cmru/skills/install/--harness/--harness | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/install/--harness"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/install/--harness", "spelling": "--harness"}} | ACCEPT | skills install --harness claude | {"status": 0, "stderr contains": "", "stdout contains": "installed cmru-cli"} | Writes only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/install/option-spelling/--harness/--harness] | Exact spelling of the harness selector (claude, agents, all). |
| case:route:entrypoint:cmru/skills/install/option-spelling/option:route:entrypoint:cmru/skills/install/--overwrite-modified/--overwrite-modified | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/install/--overwrite-modified"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/install/--overwrite-modified", "spelling": "--overwrite-modified"}} | ACCEPT | skills install --overwrite-modified | {"status": 0, "stderr contains": "", "stdout contains": "installed cmru-cli"} | Writes only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/install/option-spelling/--overwrite-modified/--overwrite-modified] | --overwrite-modified lets install replace a locally modified skill (otherwise it is never overwritten). |
| case:route:entrypoint:cmru/skills/list/exclusive-conflict/target/option:route:entrypoint:cmru/skills/list/--dest/option:route:entrypoint:cmru/skills/list/--harness | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/skills/list/--dest", "option:route:entrypoint:cmru/skills/list/--harness"], "shape": {"group_id": "target", "options": ["option:route:entrypoint:cmru/skills/list/--dest", "option:route:entrypoint:cmru/skills/list/--harness"]}} | REFUSE | skills list --dest dest --harness claude | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing is read. | tests/test_cli_review_cases.py::test_reviewed_case[skills/list/exclusive-conflict/target/--dest/--harness] | --harness and --dest are mutually exclusive destination selectors. |
| case:route:entrypoint:cmru/skills/list/exclusive-member/target/option:route:entrypoint:cmru/skills/list/--dest | exclusive-member | {"members": ["option:route:entrypoint:cmru/skills/list/--dest"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:cmru/skills/list/--dest"}} | ACCEPT | skills list --dest dest | {"status": 0, "stderr contains": "", "stdout contains": "absent"} | Read-only; nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[skills/list/exclusive-member/target/--dest] | --dest DIR lists the installed state of exactly that directory. |
| case:route:entrypoint:cmru/skills/list/exclusive-member/target/option:route:entrypoint:cmru/skills/list/--harness | exclusive-member | {"members": ["option:route:entrypoint:cmru/skills/list/--harness"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:cmru/skills/list/--harness"}} | ACCEPT | skills list --harness claude | {"status": 0, "stderr contains": "", "stdout contains": "absent"} | Read-only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/list/exclusive-member/target/--harness] | --harness selects the harness directory listed. |
| case:route:entrypoint:cmru/skills/list/minimum | minimum | {"members": [], "shape": {"defaulted_options": [], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | skills list | {"status": 0, "stderr contains": "", "stdout contains": "absent"} | Read-only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/list/minimum] | Nothing is required: it prints each packaged skill's installed state per harness and always exits 0. |
| case:route:entrypoint:cmru/skills/list/option-choice/option:route:entrypoint:cmru/skills/list/--harness/389432cbf8 | option-choice | {"members": ["option:route:entrypoint:cmru/skills/list/--harness"], "shape": {"choice": "claude", "option_id": "option:route:entrypoint:cmru/skills/list/--harness"}} | ACCEPT | skills list --harness claude | {"status": 0, "stderr contains": "", "stdout contains": "absent"} | Read-only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/list/option-choice/--harness/389432cbf8] | --harness claude selects the Claude skills directory. |
| case:route:entrypoint:cmru/skills/list/option-choice/option:route:entrypoint:cmru/skills/list/--harness/5cf7504d97 | option-choice | {"members": ["option:route:entrypoint:cmru/skills/list/--harness"], "shape": {"choice": "all", "option_id": "option:route:entrypoint:cmru/skills/list/--harness"}} | ACCEPT | skills list --harness all | {"status": 0, "stderr contains": "", "stdout contains": "absent"} | Read-only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/list/option-choice/--harness/5cf7504d97] | --harness all selects every harness directory (the default). |
| case:route:entrypoint:cmru/skills/list/option-choice/option:route:entrypoint:cmru/skills/list/--harness/bc42532430 | option-choice | {"members": ["option:route:entrypoint:cmru/skills/list/--harness"], "shape": {"choice": "agents", "option_id": "option:route:entrypoint:cmru/skills/list/--harness"}} | ACCEPT | skills list --harness agents | {"status": 0, "stderr contains": "", "stdout contains": "absent"} | Read-only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/list/option-choice/--harness/bc42532430] | --harness agents selects the shared agents skills directory. |
| case:route:entrypoint:cmru/skills/list/option-spelling/option:route:entrypoint:cmru/skills/list/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/list/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/list/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | skills list --debug-raw | {"status": 0, "stderr contains": "--debug-raw is active", "stdout contains": "absent"} | Prints the warning; read-only. | tests/test_cli_review_cases.py::test_reviewed_case[skills/list/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/skills/list/option-spelling/option:route:entrypoint:cmru/skills/list/--dest/--dest | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/list/--dest"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/list/--dest", "spelling": "--dest"}} | ACCEPT | skills list --dest dest | {"status": 0, "stderr contains": "", "stdout contains": "absent"} | Read-only; nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[skills/list/option-spelling/--dest/--dest] | Exact spelling of the explicit destination. |
| case:route:entrypoint:cmru/skills/list/option-spelling/option:route:entrypoint:cmru/skills/list/--harness/--harness | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/list/--harness"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/list/--harness", "spelling": "--harness"}} | ACCEPT | skills list --harness claude | {"status": 0, "stderr contains": "", "stdout contains": "absent"} | Read-only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/list/option-spelling/--harness/--harness] | Exact spelling of the harness selector (claude, agents, all). |
| case:route:entrypoint:cmru/skills/list/option-spelling/option:route:entrypoint:cmru/skills/list/--json/--json | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/list/--json"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/list/--json", "spelling": "--json"}} | ACCEPT | skills list --json | {"status": 0, "stderr contains": "", "stdout contains": "\"skills\""} | Read-only under the sandbox HOME; machine output only. | tests/test_cli_review_cases.py::test_reviewed_case[skills/list/option-spelling/--json/--json] | --json emits {tool, version, skills[...]} on stdout. |
| case:route:entrypoint:cmru/skills/uninstall/exclusive-conflict/target/option:route:entrypoint:cmru/skills/uninstall/--dest/option:route:entrypoint:cmru/skills/uninstall/--harness | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/skills/uninstall/--dest", "option:route:entrypoint:cmru/skills/uninstall/--harness"], "shape": {"group_id": "target", "options": ["option:route:entrypoint:cmru/skills/uninstall/--dest", "option:route:entrypoint:cmru/skills/uninstall/--harness"]}} | REFUSE | skills uninstall --dest dest --harness claude | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); nothing is removed. | tests/test_cli_review_cases.py::test_reviewed_case[skills/uninstall/exclusive-conflict/target/--dest/--harness] | --harness and --dest are mutually exclusive destination selectors. |
| case:route:entrypoint:cmru/skills/uninstall/exclusive-member/target/option:route:entrypoint:cmru/skills/uninstall/--dest | exclusive-member | {"members": ["option:route:entrypoint:cmru/skills/uninstall/--dest"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:cmru/skills/uninstall/--dest"}} | ACCEPT | skills uninstall --dest dest | {"status": 0, "stderr contains": "", "stdout contains": "skipped cmru-cli"} | Removes only the named skill under the given directory (absent here, so skipped). | tests/test_cli_review_cases.py::test_reviewed_case[skills/uninstall/exclusive-member/target/--dest] | --dest DIR removes the skill from exactly that directory. |
| case:route:entrypoint:cmru/skills/uninstall/exclusive-member/target/option:route:entrypoint:cmru/skills/uninstall/--harness | exclusive-member | {"members": ["option:route:entrypoint:cmru/skills/uninstall/--harness"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:cmru/skills/uninstall/--harness"}} | ACCEPT | skills uninstall --harness claude | {"status": 0, "stderr contains": "", "stdout contains": "skipped cmru-cli"} | Removes only under the sandbox HOME (absent here, so skipped). | tests/test_cli_review_cases.py::test_reviewed_case[skills/uninstall/exclusive-member/target/--harness] | --harness selects the harness directory the skill is removed from. |
| case:route:entrypoint:cmru/skills/uninstall/minimum | minimum | {"members": ["option:route:entrypoint:cmru/skills/uninstall/--overwrite-modified"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/skills/uninstall/--overwrite-modified"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | skills uninstall | {"status": 0, "stderr contains": "", "stdout contains": "skipped cmru-cli"} | Removes only under the sandbox HOME (absent here, so skipped). | tests/test_cli_review_cases.py::test_reviewed_case[skills/uninstall/minimum] | Nothing is required: it removes the packaged skill from every harness directory; a locally modified skill is kept without --overwrite-modified. |
| case:route:entrypoint:cmru/skills/uninstall/option-choice/option:route:entrypoint:cmru/skills/uninstall/--harness/389432cbf8 | option-choice | {"members": ["option:route:entrypoint:cmru/skills/uninstall/--harness"], "shape": {"choice": "claude", "option_id": "option:route:entrypoint:cmru/skills/uninstall/--harness"}} | ACCEPT | skills uninstall --harness claude | {"status": 0, "stderr contains": "", "stdout contains": "skipped cmru-cli"} | Removes only under the sandbox HOME (absent here, so skipped). | tests/test_cli_review_cases.py::test_reviewed_case[skills/uninstall/option-choice/--harness/389432cbf8] | --harness claude selects the Claude skills directory. |
| case:route:entrypoint:cmru/skills/uninstall/option-choice/option:route:entrypoint:cmru/skills/uninstall/--harness/5cf7504d97 | option-choice | {"members": ["option:route:entrypoint:cmru/skills/uninstall/--harness"], "shape": {"choice": "all", "option_id": "option:route:entrypoint:cmru/skills/uninstall/--harness"}} | ACCEPT | skills uninstall --harness all | {"status": 0, "stderr contains": "", "stdout contains": "skipped cmru-cli"} | Removes only under the sandbox HOME (absent here, so skipped). | tests/test_cli_review_cases.py::test_reviewed_case[skills/uninstall/option-choice/--harness/5cf7504d97] | --harness all selects every harness directory (the default). |
| case:route:entrypoint:cmru/skills/uninstall/option-choice/option:route:entrypoint:cmru/skills/uninstall/--harness/bc42532430 | option-choice | {"members": ["option:route:entrypoint:cmru/skills/uninstall/--harness"], "shape": {"choice": "agents", "option_id": "option:route:entrypoint:cmru/skills/uninstall/--harness"}} | ACCEPT | skills uninstall --harness agents | {"status": 0, "stderr contains": "", "stdout contains": "skipped cmru-cli"} | Removes only under the sandbox HOME (absent here, so skipped). | tests/test_cli_review_cases.py::test_reviewed_case[skills/uninstall/option-choice/--harness/bc42532430] | --harness agents selects the shared agents skills directory. |
| case:route:entrypoint:cmru/skills/uninstall/option-spelling/option:route:entrypoint:cmru/skills/uninstall/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/uninstall/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/uninstall/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | skills uninstall --debug-raw | {"status": 0, "stderr contains": "--debug-raw is active", "stdout contains": "skipped cmru-cli"} | Prints the warning; removes only under the sandbox HOME. | tests/test_cli_review_cases.py::test_reviewed_case[skills/uninstall/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/skills/uninstall/option-spelling/option:route:entrypoint:cmru/skills/uninstall/--dest/--dest | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/uninstall/--dest"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/uninstall/--dest", "spelling": "--dest"}} | ACCEPT | skills uninstall --dest dest | {"status": 0, "stderr contains": "", "stdout contains": "skipped cmru-cli"} | Removes only the named skill under the given directory (absent here, so skipped). | tests/test_cli_review_cases.py::test_reviewed_case[skills/uninstall/option-spelling/--dest/--dest] | Exact spelling of the explicit destination. |
| case:route:entrypoint:cmru/skills/uninstall/option-spelling/option:route:entrypoint:cmru/skills/uninstall/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/uninstall/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/uninstall/--dry-run", "spelling": "--dry-run"}} | ACCEPT | skills uninstall --dry-run | {"status": 0, "stderr contains": "", "stdout contains": "would skip cmru-cli"} | Removes nothing. | tests/test_cli_review_cases.py::test_reviewed_case[skills/uninstall/option-spelling/--dry-run/--dry-run] | --dry-run reports what would be removed without changing anything. |
| case:route:entrypoint:cmru/skills/uninstall/option-spelling/option:route:entrypoint:cmru/skills/uninstall/--harness/--harness | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/uninstall/--harness"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/uninstall/--harness", "spelling": "--harness"}} | ACCEPT | skills uninstall --harness claude | {"status": 0, "stderr contains": "", "stdout contains": "skipped cmru-cli"} | Removes only under the sandbox HOME (absent here, so skipped). | tests/test_cli_review_cases.py::test_reviewed_case[skills/uninstall/option-spelling/--harness/--harness] | Exact spelling of the harness selector (claude, agents, all). |
| case:route:entrypoint:cmru/skills/uninstall/option-spelling/option:route:entrypoint:cmru/skills/uninstall/--overwrite-modified/--overwrite-modified | option-spelling | {"members": ["option:route:entrypoint:cmru/skills/uninstall/--overwrite-modified"], "shape": {"option_id": "option:route:entrypoint:cmru/skills/uninstall/--overwrite-modified", "spelling": "--overwrite-modified"}} | ACCEPT | skills uninstall --overwrite-modified | {"status": 0, "stderr contains": "", "stdout contains": "skipped cmru-cli"} | Removes only under the sandbox HOME (absent here, so skipped). | tests/test_cli_review_cases.py::test_reviewed_case[skills/uninstall/option-spelling/--overwrite-modified/--overwrite-modified] | --overwrite-modified lets uninstall remove a locally modified skill (otherwise it is kept). |
| case:route:entrypoint:cmru/standards/argument-shape/argument:route:entrypoint:cmru/standards/target | argument-shape | {"members": ["argument:route:entrypoint:cmru/standards/target"], "shape": {"argument_id": "argument:route:entrypoint:cmru/standards/target"}} | ACCEPT | standards demo | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is checked or written. | tests/test_cli_review_cases.py::test_reviewed_case[standards/argument-shape/target] | The optional target chooses the projects checked or updated. |
| case:route:entrypoint:cmru/standards/constraint-requires/1 | constraint-requires | {"members": ["option:route:entrypoint:cmru/standards/--dry-run", "option:route:entrypoint:cmru/standards/--update"], "shape": {"any_of": ["--update"], "kind": "requires", "option": "--dry-run", "reason": "without --update the check writes nothing, so there is nothing to preview"}} | REFUSE | standards --dry-run | {"status": 2, "stderr contains": "--dry-run requires --update", "stdout contains": ""} | Refused as a usage error (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[standards/constraint-requires/1] | Without --update the check writes nothing, so --dry-run requires --update. |
| case:route:entrypoint:cmru/standards/minimum | minimum | {"members": ["option:route:entrypoint:cmru/standards/--log-prefix-time-short", "option:route:entrypoint:cmru/standards/--update"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/standards/--log-prefix-time-short", "option:route:entrypoint:cmru/standards/--update"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | standards | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[standards/minimum] | Bare `standards` is the read-only conformance check (exit 4 when issues are found: refused by policy, nothing changed); --update is the opt-in mutation. |
| case:route:entrypoint:cmru/standards/option-spelling/option:route:entrypoint:cmru/standards/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/standards/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/standards/--config", "spelling": "--config"}} | REFUSE | standards --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[standards/option-spelling/--config/--config] | --config selects the project contract; only the two canonical names are accepted. |
| case:route:entrypoint:cmru/standards/option-spelling/option:route:entrypoint:cmru/standards/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/standards/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/standards/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | standards --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[standards/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/standards/option-spelling/option:route:entrypoint:cmru/standards/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/standards/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/standards/--dry-run", "spelling": "--dry-run"}} | ACCEPT | standards --update --dry-run | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); with a config the preview writes nothing. | tests/test_cli_review_cases.py::test_reviewed_case[standards/option-spelling/--dry-run/--dry-run] | With --update, --dry-run prints the template-marker diffs without writing. |
| case:route:entrypoint:cmru/standards/option-spelling/option:route:entrypoint:cmru/standards/--json/--json | option-spelling | {"members": ["option:route:entrypoint:cmru/standards/--json"], "shape": {"option_id": "option:route:entrypoint:cmru/standards/--json", "spelling": "--json"}} | ACCEPT | standards --json | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing printed on stdout. | tests/test_cli_review_cases.py::test_reviewed_case[standards/option-spelling/--json/--json] | --json prints one {schema_version, conforms, projects[...]} document on stdout (human lines move to stderr). |
| case:route:entrypoint:cmru/standards/option-spelling/option:route:entrypoint:cmru/standards/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/standards/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/standards/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | standards --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[standards/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/standards/option-spelling/option:route:entrypoint:cmru/standards/--update/--update | option-spelling | {"members": ["option:route:entrypoint:cmru/standards/--update"], "shape": {"option_id": "option:route:entrypoint:cmru/standards/--update", "spelling": "--update"}} | ACCEPT | standards --update | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[standards/option-spelling/--update/--update] | --update writes only CMRU-owned template revision markers; it is the single opt-in mutation. |
| case:route:entrypoint:cmru/status/argument-shape/argument:route:entrypoint:cmru/status/target | argument-shape | {"members": ["argument:route:entrypoint:cmru/status/target"], "shape": {"argument_id": "argument:route:entrypoint:cmru/status/target"}} | ACCEPT | status demo | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[status/argument-shape/target] | The optional target chooses the release view; --json lists EVERY selected project, changed or not. |
| case:route:entrypoint:cmru/status/exclusive-conflict/version-override/option:route:entrypoint:cmru/status/--major/option:route:entrypoint:cmru/status/--minor | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/status/--major", "option:route:entrypoint:cmru/status/--minor"], "shape": {"group_id": "version-override", "options": ["option:route:entrypoint:cmru/status/--major", "option:route:entrypoint:cmru/status/--minor"]}} | REFUSE | status --major --minor | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[status/exclusive-conflict/version-override/--major/--minor] | --minor, --major and --set-version are mutually exclusive preview selectors. |
| case:route:entrypoint:cmru/status/exclusive-conflict/version-override/option:route:entrypoint:cmru/status/--major/option:route:entrypoint:cmru/status/--set-version | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/status/--major", "option:route:entrypoint:cmru/status/--set-version"], "shape": {"group_id": "version-override", "options": ["option:route:entrypoint:cmru/status/--major", "option:route:entrypoint:cmru/status/--set-version"]}} | REFUSE | status --major --set-version 1.2.3 | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[status/exclusive-conflict/version-override/--major/--set-version] | --minor, --major and --set-version are mutually exclusive preview selectors. |
| case:route:entrypoint:cmru/status/exclusive-conflict/version-override/option:route:entrypoint:cmru/status/--minor/option:route:entrypoint:cmru/status/--set-version | exclusive-conflict | {"members": ["option:route:entrypoint:cmru/status/--minor", "option:route:entrypoint:cmru/status/--set-version"], "shape": {"group_id": "version-override", "options": ["option:route:entrypoint:cmru/status/--minor", "option:route:entrypoint:cmru/status/--set-version"]}} | REFUSE | status --minor --set-version 1.2.3 | {"status": 2, "stderr contains": "not allowed with argument", "stdout contains": ""} | Refused during argument parsing (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[status/exclusive-conflict/version-override/--minor/--set-version] | --minor, --major and --set-version are mutually exclusive preview selectors. |
| case:route:entrypoint:cmru/status/exclusive-member/version-override/option:route:entrypoint:cmru/status/--major | exclusive-member | {"members": ["option:route:entrypoint:cmru/status/--major"], "shape": {"group_id": "version-override", "required": false, "selected_option": "option:route:entrypoint:cmru/status/--major"}} | ACCEPT | status --major | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[status/exclusive-member/version-override/--major] | --major previews a major bump; rejected for external-version/no-tag projects. |
| case:route:entrypoint:cmru/status/exclusive-member/version-override/option:route:entrypoint:cmru/status/--minor | exclusive-member | {"members": ["option:route:entrypoint:cmru/status/--minor"], "shape": {"group_id": "version-override", "required": false, "selected_option": "option:route:entrypoint:cmru/status/--minor"}} | ACCEPT | status --minor | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[status/exclusive-member/version-override/--minor] | --minor previews a minor bump; rejected for external-version/no-tag projects. |
| case:route:entrypoint:cmru/status/exclusive-member/version-override/option:route:entrypoint:cmru/status/--set-version | exclusive-member | {"members": ["option:route:entrypoint:cmru/status/--set-version"], "shape": {"group_id": "version-override", "required": false, "selected_option": "option:route:entrypoint:cmru/status/--set-version"}} | ACCEPT | status --set-version 1.2.3 | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[status/exclusive-member/version-override/--set-version] | --set-version VER previews an explicit version. |
| case:route:entrypoint:cmru/status/minimum | minimum | {"members": ["option:route:entrypoint:cmru/status/--log-prefix-time-short", "option:route:entrypoint:cmru/status/--minor", "option:route:entrypoint:cmru/status/--major"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/status/--log-prefix-time-short", "option:route:entrypoint:cmru/status/--minor", "option:route:entrypoint:cmru/status/--major"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | status | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[status/minimum] | Nothing is required; status is read-only (no --dry-run, no --show-run-details/--log-append) and never touches cmru.release.log. |
| case:route:entrypoint:cmru/status/option-spelling/option:route:entrypoint:cmru/status/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/status/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/status/--config", "spelling": "--config"}} | REFUSE | status --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[status/option-spelling/--config/--config] | --config selects the release view's contract; only the two canonical names are accepted. |
| case:route:entrypoint:cmru/status/option-spelling/option:route:entrypoint:cmru/status/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/status/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/status/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | status --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[status/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/status/option-spelling/option:route:entrypoint:cmru/status/--json/--json | option-spelling | {"members": ["option:route:entrypoint:cmru/status/--json"], "shape": {"option_id": "option:route:entrypoint:cmru/status/--json", "spelling": "--json"}} | ACCEPT | status --json | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[status/option-spelling/--json/--json] | --json emits one list with a record per selected project (project, changed, last_tag, bump, next_version, note). |
| case:route:entrypoint:cmru/status/option-spelling/option:route:entrypoint:cmru/status/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/status/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/status/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | status --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[status/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/status/option-spelling/option:route:entrypoint:cmru/status/--major/--major | option-spelling | {"members": ["option:route:entrypoint:cmru/status/--major"], "shape": {"option_id": "option:route:entrypoint:cmru/status/--major", "spelling": "--major"}} | ACCEPT | status --major | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[status/option-spelling/--major/--major] | Exact spelling of the major-bump preview. |
| case:route:entrypoint:cmru/status/option-spelling/option:route:entrypoint:cmru/status/--minor/--minor | option-spelling | {"members": ["option:route:entrypoint:cmru/status/--minor"], "shape": {"option_id": "option:route:entrypoint:cmru/status/--minor", "spelling": "--minor"}} | ACCEPT | status --minor | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[status/option-spelling/--minor/--minor] | Exact spelling of the minor-bump preview. |
| case:route:entrypoint:cmru/status/option-spelling/option:route:entrypoint:cmru/status/--ref/--ref | option-spelling | {"members": ["option:route:entrypoint:cmru/status/--ref"], "shape": {"option_id": "option:route:entrypoint:cmru/status/--ref", "spelling": "--ref"}} | ACCEPT | status --ref main | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[status/option-spelling/--ref/--ref] | --ref REF selects the comparison ref for the release view. |
| case:route:entrypoint:cmru/status/option-spelling/option:route:entrypoint:cmru/status/--set-version/--set-version | option-spelling | {"members": ["option:route:entrypoint:cmru/status/--set-version"], "shape": {"option_id": "option:route:entrypoint:cmru/status/--set-version", "spelling": "--set-version"}} | ACCEPT | status --set-version 1.2.3 | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[status/option-spelling/--set-version/--set-version] | Exact spelling of the explicit-version preview. |
| case:route:entrypoint:cmru/tester-gate/argument-shape/argument:route:entrypoint:cmru/tester-gate/command | argument-shape | {"members": ["argument:route:entrypoint:cmru/tester-gate/command"], "shape": {"argument_id": "argument:route:entrypoint:cmru/tester-gate/command"}} | ACCEPT | tester-gate --cwd missing-dir -- true | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | With no CMRU_TESTER_* configuration it refuses (exit 3, naming the missing variables) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/argument-shape/command] | The positional command... is the command after `--`, run inside the tester container. |
| case:route:entrypoint:cmru/tester-gate/minimum | minimum | {"members": ["option:route:entrypoint:cmru/tester-gate/--log-prefix-time-short", "option:route:entrypoint:cmru/tester-gate/--cwd", "option:route:entrypoint:cmru/tester-gate/--enable-docker"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/tester-gate/--log-prefix-time-short", "option:route:entrypoint:cmru/tester-gate/--enable-docker"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": ["option:route:entrypoint:cmru/tester-gate/--cwd"]}} | ACCEPT | tester-gate --cwd missing-dir | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses (exit 3) before any host probe or docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/minimum] | --cwd DIR (the in-container checkout path) is the only required option; every other input resolves explicit option, then the variable named in the option's help, and a missing required value exits 3 (CLI-17). |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--cgroup-parent/--cgroup-parent | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--cgroup-parent"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--cgroup-parent", "spelling": "--cgroup-parent"}} | ACCEPT | tester-gate --cwd missing-dir --cgroup-parent gates.slice | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--cgroup-parent/--cgroup-parent] | --cgroup-parent overrides the required gates slice; every helper and workload container receives it. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--cgroup-probe-image/--cgroup-probe-image | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--cgroup-probe-image"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--cgroup-probe-image", "spelling": "--cgroup-probe-image"}} | ACCEPT | tester-gate --cwd missing-dir --cgroup-probe-image probe@sha256:0000000000000000000000000000000000000000000000000000000000000000 | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--cgroup-probe-image/--cgroup-probe-image] | --cgroup-probe-image selects the privileged helper image; it MUST be digest-pinned (<repo>@sha256:<64 hex>) and is started with --pull=never. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--cpus/--cpus | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--cpus"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--cpus", "spelling": "--cpus"}} | ACCEPT | tester-gate --cwd missing-dir --cpus 1 | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--cpus/--cpus] | --cpus N is a finite decimal of at least 0.00001 Docker can represent (used without --cpu-period); zero, smaller, non-finite or unrepresentable values are refused before host probes. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--cwd/--cwd | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--cwd"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--cwd", "spelling": "--cwd"}} | ACCEPT | tester-gate --cwd missing-dir | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--cwd/--cwd] | --cwd DIR selects the in-container checkout path. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | tester-gate --cwd missing-dir --debug-raw | {"status": 3, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then refuses on missing configuration (exit 3). | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--device-read-bps/--device-read-bps | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--device-read-bps"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--device-read-bps", "spelling": "--device-read-bps"}} | ACCEPT | tester-gate --cwd missing-dir --device-read-bps /dev/null:1 | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--device-read-bps/--device-read-bps] | --device-read-bps DEV:RATE sets a device read-bandwidth limit. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--device-read-iops/--device-read-iops | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--device-read-iops"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--device-read-iops", "spelling": "--device-read-iops"}} | ACCEPT | tester-gate --cwd missing-dir --device-read-iops /dev/null:1 | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--device-read-iops/--device-read-iops] | --device-read-iops DEV:RATE sets a device read-IOPS limit. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--device-write-bps/--device-write-bps | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--device-write-bps"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--device-write-bps", "spelling": "--device-write-bps"}} | ACCEPT | tester-gate --cwd missing-dir --device-write-bps /dev/null:1 | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--device-write-bps/--device-write-bps] | --device-write-bps DEV:RATE sets a device write-bandwidth limit. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--device-write-iops/--device-write-iops | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--device-write-iops"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--device-write-iops", "spelling": "--device-write-iops"}} | ACCEPT | tester-gate --cwd missing-dir --device-write-iops /dev/null:1 | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--device-write-iops/--device-write-iops] | --device-write-iops DEV:RATE sets a device write-IOPS limit. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--dind-cpus/--dind-cpus | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--dind-cpus"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--dind-cpus", "spelling": "--dind-cpus"}} | ACCEPT | tester-gate --cwd missing-dir --dind-cpus 1 | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--dind-cpus/--dind-cpus] | --dind-cpus bounds the DinD sidecar CPU (required with --enable-docker; CMRU_TESTER_DIND_*). |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--dind-image/--dind-image | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--dind-image"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--dind-image", "spelling": "--dind-image"}} | ACCEPT | tester-gate --cwd missing-dir --dind-image dind@sha256:0000000000000000000000000000000000000000000000000000000000000000 | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--dind-image/--dind-image] | --dind-image selects the privileged DinD helper image; it MUST be digest-pinned and is started with --pull=never. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--dind-memory/--dind-memory | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--dind-memory"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--dind-memory", "spelling": "--dind-memory"}} | ACCEPT | tester-gate --cwd missing-dir --dind-memory 1g | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--dind-memory/--dind-memory] | --dind-memory bounds the DinD sidecar memory separately from the workload's (the nested envelope is never shared or doubled). |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--dind-pids-limit/--dind-pids-limit | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--dind-pids-limit"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--dind-pids-limit", "spelling": "--dind-pids-limit"}} | ACCEPT | tester-gate --cwd missing-dir --dind-pids-limit 100 | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--dind-pids-limit/--dind-pids-limit] | --dind-pids-limit bounds the DinD sidecar process count (required with --enable-docker). |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--dry-run", "spelling": "--dry-run"}} | ACCEPT | tester-gate --cwd missing-dir --dry-run | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on missing configuration (exit 3); a configured preview starts no container. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--dry-run/--dry-run] | --dry-run prints the workload argv (and the DinD startup argv when enabled), starts no container and skips privileged host slice/IO probes; required configuration is still resolved first. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--enable-docker/--enable-docker | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--enable-docker"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--enable-docker", "spelling": "--enable-docker"}} | ACCEPT | tester-gate --cwd missing-dir --enable-docker | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--enable-docker/--enable-docker] | --enable-docker requests a DinD sidecar and requires its image and limits. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--forward-background-slice/--forward-background-slice | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--forward-background-slice"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--forward-background-slice", "spelling": "--forward-background-slice"}} | ACCEPT | tester-gate --cwd missing-dir --forward-background-slice BG_SLICE | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--forward-background-slice/--forward-background-slice] | --forward-background-slice controls nested slice-fact forwarding (renamed from --forward-cgroup-parent-var). |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--forward-gates-slice/--forward-gates-slice | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--forward-gates-slice"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--forward-gates-slice", "spelling": "--forward-gates-slice"}} | ACCEPT | tester-gate --cwd missing-dir --forward-gates-slice GATES_SLICE | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--forward-gates-slice/--forward-gates-slice] | --forward-gates-slice controls nested gates-slice forwarding (renamed from --forward-cgroup-parent-gates-var). |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--image/--image | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--image"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--image", "spelling": "--image"}} | ACCEPT | tester-gate --cwd missing-dir --image tester:1 | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on the other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--image/--image] | --image selects the pinned tester image (otherwise CMRU_TESTER_UNIFIED_IMAGE); an image reference starting with `-` is refused. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | tester-gate --cwd missing-dir --log-prefix-time-short | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Presentation only; refuses on missing configuration (exit 3). | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--memory-swap/--memory-swap | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--memory-swap"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--memory-swap", "spelling": "--memory-swap"}} | ACCEPT | tester-gate --cwd missing-dir --memory-swap 2g | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--memory-swap/--memory-swap] | --memory-swap sets the container memory+swap bound (Docker refuses --memory larger than --memory-swap). |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--memory/--memory | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--memory"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--memory", "spelling": "--memory"}} | ACCEPT | tester-gate --cwd missing-dir --memory 1g | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--memory/--memory] | --memory sets the container memory bound. |
| case:route:entrypoint:cmru/tester-gate/option-spelling/option:route:entrypoint:cmru/tester-gate/--pids-limit/--pids-limit | option-spelling | {"members": ["option:route:entrypoint:cmru/tester-gate/--pids-limit"], "shape": {"option_id": "option:route:entrypoint:cmru/tester-gate/--pids-limit", "spelling": "--pids-limit"}} | ACCEPT | tester-gate --cwd missing-dir --pids-limit 100 | {"status": 3, "stderr contains": "missing required configuration", "stdout contains": ""} | Refuses on other missing configuration (exit 3) before any docker command. | tests/test_cli_review_cases.py::test_reviewed_case[tester-gate/option-spelling/--pids-limit/--pids-limit] | --pids-limit (required, CMRU_TESTER_PIDS_LIMIT, positive integer, no default) caps the workload's process count. |
| case:route:entrypoint:cmru/tool-deps/argument-shape/argument:route:entrypoint:cmru/tool-deps/target | argument-shape | {"members": ["argument:route:entrypoint:cmru/tool-deps/target"], "shape": {"argument_id": "argument:route:entrypoint:cmru/tool-deps/target"}} | ACCEPT | tool-deps demo | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no network request. | tests/test_cli_review_cases.py::test_reviewed_case[tool-deps/argument-shape/target] | The optional target selects the tool-dependency declarations. |
| case:route:entrypoint:cmru/tool-deps/constraint-conflict/2 | constraint-conflict | {"members": ["option:route:entrypoint:cmru/tool-deps/--refresh", "option:route:entrypoint:cmru/tool-deps/--json"], "shape": {"kind": "conflicts", "options": ["--refresh", "--json"], "reason": "a refresh prints progress lines, not a report"}} | REFUSE | tool-deps --refresh provider/project --json | {"status": 2, "stderr contains": "cannot be used together", "stdout contains": ""} | Refused as a usage error (exit 2); nothing is fetched or written. | tests/test_cli_review_cases.py::test_reviewed_case[tool-deps/constraint-conflict/2] | A refresh prints progress lines, not a report: --refresh and --json conflict. |
| case:route:entrypoint:cmru/tool-deps/constraint-conflict/3 | constraint-conflict | {"members": ["option:route:entrypoint:cmru/tool-deps/--refresh", "option:route:entrypoint:cmru/tool-deps/--allow-stale-tool-deps"], "shape": {"kind": "conflicts", "options": ["--refresh", "--allow-stale-tool-deps"], "reason": "a refresh re-vendors the latest artifact, so staleness cannot be allowed"}} | REFUSE | tool-deps --refresh provider/project --allow-stale-tool-deps | {"status": 2, "stderr contains": "cannot be used together", "stdout contains": ""} | Refused as a usage error (exit 2); nothing is fetched or written. | tests/test_cli_review_cases.py::test_reviewed_case[tool-deps/constraint-conflict/3] | A refresh re-vendors the latest artifact, so freshness relaxation has no meaning: --refresh and --allow-stale-tool-deps conflict. |
| case:route:entrypoint:cmru/tool-deps/constraint-requires/1 | constraint-requires | {"members": ["option:route:entrypoint:cmru/tool-deps/--dry-run", "option:route:entrypoint:cmru/tool-deps/--refresh"], "shape": {"any_of": ["--refresh"], "kind": "requires", "option": "--dry-run", "reason": "without --refresh the check writes nothing, so there is nothing to preview"}} | REFUSE | tool-deps --dry-run | {"status": 2, "stderr contains": "--dry-run requires --refresh", "stdout contains": ""} | Refused as a usage error (exit 2); nothing is fetched or written. | tests/test_cli_review_cases.py::test_reviewed_case[tool-deps/constraint-requires/1] | Without --refresh the check writes nothing, so --dry-run requires --refresh. |
| case:route:entrypoint:cmru/tool-deps/minimum | minimum | {"members": ["option:route:entrypoint:cmru/tool-deps/--log-prefix-time-short", "option:route:entrypoint:cmru/tool-deps/--allow-stale-tool-deps", "option:route:entrypoint:cmru/tool-deps/--timeout"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/tool-deps/--log-prefix-time-short", "option:route:entrypoint:cmru/tool-deps/--allow-stale-tool-deps", "option:route:entrypoint:cmru/tool-deps/--timeout"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | tool-deps | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no network request. | tests/test_cli_review_cases.py::test_reviewed_case[tool-deps/minimum] | Bare `tool-deps` verifies declared pins (read-only); a blocking stale or mismatched dependency exits 4 (refused by verification policy); --refresh is the opt-in mutation. |
| case:route:entrypoint:cmru/tool-deps/option-spelling/option:route:entrypoint:cmru/tool-deps/--allow-stale-tool-deps/--allow-stale-tool-deps | option-spelling | {"members": ["option:route:entrypoint:cmru/tool-deps/--allow-stale-tool-deps"], "shape": {"option_id": "option:route:entrypoint:cmru/tool-deps/--allow-stale-tool-deps", "spelling": "--allow-stale-tool-deps"}} | ACCEPT | tool-deps --allow-stale-tool-deps | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no network request. | tests/test_cli_review_cases.py::test_reviewed_case[tool-deps/option-spelling/--allow-stale-tool-deps/--allow-stale-tool-deps] | --allow-stale-tool-deps relaxes freshness only during verification. |
| case:route:entrypoint:cmru/tool-deps/option-spelling/option:route:entrypoint:cmru/tool-deps/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/tool-deps/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/tool-deps/--config", "spelling": "--config"}} | REFUSE | tool-deps --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2); no network request. | tests/test_cli_review_cases.py::test_reviewed_case[tool-deps/option-spelling/--config/--config] | --config selects the declarations' contract; only the two canonical names are accepted. |
| case:route:entrypoint:cmru/tool-deps/option-spelling/option:route:entrypoint:cmru/tool-deps/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/tool-deps/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/tool-deps/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | tool-deps --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[tool-deps/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/tool-deps/option-spelling/option:route:entrypoint:cmru/tool-deps/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/tool-deps/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/tool-deps/--dry-run", "spelling": "--dry-run"}} | ACCEPT | tool-deps --refresh provider/project --dry-run | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no fetch happens. | tests/test_cli_review_cases.py::test_reviewed_case[tool-deps/option-spelling/--dry-run/--dry-run] | With --refresh, --dry-run fetches/verifies the proposed artifact and prints the planned file/config changes without writing. |
| case:route:entrypoint:cmru/tool-deps/option-spelling/option:route:entrypoint:cmru/tool-deps/--json/--json | option-spelling | {"members": ["option:route:entrypoint:cmru/tool-deps/--json"], "shape": {"option_id": "option:route:entrypoint:cmru/tool-deps/--json", "spelling": "--json"}} | ACCEPT | tool-deps --json | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing printed. | tests/test_cli_review_cases.py::test_reviewed_case[tool-deps/option-spelling/--json/--json] | --json renders verification results as JSON (refused together with --refresh). |
| case:route:entrypoint:cmru/tool-deps/option-spelling/option:route:entrypoint:cmru/tool-deps/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/tool-deps/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/tool-deps/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | tool-deps --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[tool-deps/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/tool-deps/option-spelling/option:route:entrypoint:cmru/tool-deps/--refresh/--refresh | option-spelling | {"members": ["option:route:entrypoint:cmru/tool-deps/--refresh"], "shape": {"option_id": "option:route:entrypoint:cmru/tool-deps/--refresh", "spelling": "--refresh"}} | ACCEPT | tool-deps --refresh provider/project | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is fetched or written. | tests/test_cli_review_cases.py::test_reviewed_case[tool-deps/option-spelling/--refresh/--refresh] | --refresh PROJECT replaces the selected pins and hashes; it refuses --json and --allow-stale-tool-deps. |
| case:route:entrypoint:cmru/tool-deps/option-spelling/option:route:entrypoint:cmru/tool-deps/--timeout/--timeout | option-spelling | {"members": ["option:route:entrypoint:cmru/tool-deps/--timeout"], "shape": {"option_id": "option:route:entrypoint:cmru/tool-deps/--timeout", "spelling": "--timeout"}} | ACCEPT | tool-deps --timeout 5 | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); no network request. | tests/test_cli_review_cases.py::test_reviewed_case[tool-deps/option-spelling/--timeout/--timeout] | --timeout SECONDS bounds each network request (default 10). |
| case:route:entrypoint:cmru/versions/check/argument-shape/argument:route:entrypoint:cmru/versions/check/target | argument-shape | {"members": ["argument:route:entrypoint:cmru/versions/check/target"], "shape": {"argument_id": "argument:route:entrypoint:cmru/versions/check/target"}} | ACCEPT | versions check demo | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[versions/check/argument-shape/target] | The optional target selects the recorded target set; registry reads are fresh and read-only. |
| case:route:entrypoint:cmru/versions/check/minimum | minimum | {"members": ["option:route:entrypoint:cmru/versions/check/--log-prefix-time-short"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/versions/check/--log-prefix-time-short"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | versions check | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[versions/check/minimum] | Nothing is required; `versions check` is read-only (no --dry-run offered). |
| case:route:entrypoint:cmru/versions/check/option-spelling/option:route:entrypoint:cmru/versions/check/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/versions/check/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/versions/check/--config", "spelling": "--config"}} | REFUSE | versions check --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[versions/check/option-spelling/--config/--config] | --config selects the target set's contract; only the two canonical names are accepted. |
| case:route:entrypoint:cmru/versions/check/option-spelling/option:route:entrypoint:cmru/versions/check/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/versions/check/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/versions/check/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | versions check --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[versions/check/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/versions/check/option-spelling/option:route:entrypoint:cmru/versions/check/--json/--json | option-spelling | {"members": ["option:route:entrypoint:cmru/versions/check/--json"], "shape": {"option_id": "option:route:entrypoint:cmru/versions/check/--json", "spelling": "--json"}} | ACCEPT | versions check --json | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[versions/check/option-spelling/--json/--json] | --json is a real read mode (machine output), not a dry-run substitute. |
| case:route:entrypoint:cmru/versions/check/option-spelling/option:route:entrypoint:cmru/versions/check/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/versions/check/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/versions/check/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | versions check --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[versions/check/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/versions/init/argument-shape/argument:route:entrypoint:cmru/versions/init/target | argument-shape | {"members": ["argument:route:entrypoint:cmru/versions/init/target"], "shape": {"argument_id": "argument:route:entrypoint:cmru/versions/init/target"}} | ACCEPT | versions init demo | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[versions/init/argument-shape/target] | The optional target selects manifests from which version targets are derived. |
| case:route:entrypoint:cmru/versions/init/minimum | minimum | {"members": ["option:route:entrypoint:cmru/versions/init/--log-prefix-time-short"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/versions/init/--log-prefix-time-short"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | versions init | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[versions/init/minimum] | Nothing is required; init is the explicit target-creation operation (never implicit in build or release). |
| case:route:entrypoint:cmru/versions/init/option-spelling/option:route:entrypoint:cmru/versions/init/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/versions/init/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/versions/init/--config", "spelling": "--config"}} | REFUSE | versions init --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[versions/init/option-spelling/--config/--config] | --config selects the manifests' contract; only the two canonical names are accepted. |
| case:route:entrypoint:cmru/versions/init/option-spelling/option:route:entrypoint:cmru/versions/init/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/versions/init/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/versions/init/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | versions init --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[versions/init/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/versions/init/option-spelling/option:route:entrypoint:cmru/versions/init/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/versions/init/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/versions/init/--dry-run", "spelling": "--dry-run"}} | ACCEPT | versions init --dry-run | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); with a config the preview writes nothing. | tests/test_cli_review_cases.py::test_reviewed_case[versions/init/option-spelling/--dry-run/--dry-run] | --dry-run reports the prospective target changes without writing. |
| case:route:entrypoint:cmru/versions/init/option-spelling/option:route:entrypoint:cmru/versions/init/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/versions/init/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/versions/init/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | versions init --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[versions/init/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/versions/resolve/argument-shape/argument:route:entrypoint:cmru/versions/resolve/target | argument-shape | {"members": ["argument:route:entrypoint:cmru/versions/resolve/target"], "shape": {"argument_id": "argument:route:entrypoint:cmru/versions/resolve/target"}} | ACCEPT | versions resolve demo | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[versions/resolve/argument-shape/target] | The optional target selects version resolution; normal execution writes the declared version record and native artifacts. |
| case:route:entrypoint:cmru/versions/resolve/minimum | minimum | {"members": ["option:route:entrypoint:cmru/versions/resolve/--log-prefix-time-short"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/versions/resolve/--log-prefix-time-short"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | versions resolve | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[versions/resolve/minimum] | Nothing is required; resolution is never implicit in build or release. |
| case:route:entrypoint:cmru/versions/resolve/option-spelling/option:route:entrypoint:cmru/versions/resolve/--config/--config | option-spelling | {"members": ["option:route:entrypoint:cmru/versions/resolve/--config"], "shape": {"option_id": "option:route:entrypoint:cmru/versions/resolve/--config", "spelling": "--config"}} | REFUSE | versions resolve --config missing.toml | {"status": 2, "stderr contains": "must be named cmru.toml or cmru.orchestration.toml", "stdout contains": ""} | Refuses on the file name (exit 2); nothing is written. | tests/test_cli_review_cases.py::test_reviewed_case[versions/resolve/option-spelling/--config/--config] | --config selects the version contract; only the two canonical names are accepted. |
| case:route:entrypoint:cmru/versions/resolve/option-spelling/option:route:entrypoint:cmru/versions/resolve/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/versions/resolve/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/versions/resolve/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | versions resolve --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[versions/resolve/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/versions/resolve/option-spelling/option:route:entrypoint:cmru/versions/resolve/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:cmru/versions/resolve/--dry-run"], "shape": {"option_id": "option:route:entrypoint:cmru/versions/resolve/--dry-run", "spelling": "--dry-run"}} | ACCEPT | versions resolve --dry-run | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Stops at configuration discovery (exit 2); with a config no file is written. | tests/test_cli_review_cases.py::test_reviewed_case[versions/resolve/option-spelling/--dry-run/--dry-run] | --dry-run performs the read/derivation path and suppresses every declared file write. |
| case:route:entrypoint:cmru/versions/resolve/option-spelling/option:route:entrypoint:cmru/versions/resolve/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/versions/resolve/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/versions/resolve/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | versions resolve --log-prefix-time-short | {"status": 2, "stderr contains": "config file not found", "stdout contains": ""} | Presentation only; stops at configuration discovery (exit 2). | tests/test_cli_review_cases.py::test_reviewed_case[versions/resolve/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |
| case:route:entrypoint:cmru/worktrees/minimum | minimum | {"members": ["option:route:entrypoint:cmru/worktrees/--log-prefix-time-short"], "shape": {"defaulted_options": ["option:route:entrypoint:cmru/worktrees/--log-prefix-time-short"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | worktrees | {"status": 2, "stderr contains": "must run inside the repository", "stdout contains": ""} | Outside a git repository it refuses (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[worktrees/minimum] | No positional target; read-only listing of retained build/release worktrees. It must run inside the repository whose worktrees are inspected. |
| case:route:entrypoint:cmru/worktrees/option-spelling/option:route:entrypoint:cmru/worktrees/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:cmru/worktrees/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:cmru/worktrees/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | worktrees --debug-raw | {"status": 2, "stderr contains": "--debug-raw is active", "stdout contains": ""} | Prints the warning, then refuses outside a repository (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[worktrees/option-spelling/--debug-raw/--debug-raw] | Library control: --debug-raw lifts diagnostic redaction and warns. |
| case:route:entrypoint:cmru/worktrees/option-spelling/option:route:entrypoint:cmru/worktrees/--json/--json | option-spelling | {"members": ["option:route:entrypoint:cmru/worktrees/--json"], "shape": {"option_id": "option:route:entrypoint:cmru/worktrees/--json", "spelling": "--json"}} | ACCEPT | worktrees --json | {"status": 2, "stderr contains": "must run inside the repository", "stdout contains": ""} | Outside a git repository it refuses (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[worktrees/option-spelling/--json/--json] | --json emits machine-readable retained worktree records (a genuine output mode, not a mutation selector). |
| case:route:entrypoint:cmru/worktrees/option-spelling/option:route:entrypoint:cmru/worktrees/--log-prefix-time-short/--log-prefix-time-short | option-spelling | {"members": ["option:route:entrypoint:cmru/worktrees/--log-prefix-time-short"], "shape": {"option_id": "option:route:entrypoint:cmru/worktrees/--log-prefix-time-short", "spelling": "--log-prefix-time-short"}} | ACCEPT | worktrees --log-prefix-time-short | {"status": 2, "stderr contains": "must run inside the repository", "stdout contains": ""} | Presentation only; refuses outside a repository (exit 2); read-only. | tests/test_cli_review_cases.py::test_reviewed_case[worktrees/option-spelling/--log-prefix-time-short/--log-prefix-time-short] | Consumer-global presentation option; output only. |

**Surface inventory is incomplete:**

- `cmru skills: delegated parser does not register inherited global option(s): --log-prefix-time-short`

### Open review findings

- **minor** `adoption-skills-global-option` (route: route:entrypoint:cmru/skills): `cmru skills` does not accept the root global option --log-prefix-time-short (cli-extended 0.2.0 register_skills_verbs builds its child registry without global_options; CLI-EXT-26). `surface check` and audit AC-17/AC-18 report it. Remedy: Accepted for cmru 6.0.0 by controller ruling: do not re-implement the group with private helpers. When cli-extended ships the CLI-EXT-26 fix, bump the cli-extended floor, delete the tolerance in tests/test_cli_spec_inventory.py (backlog KI-61) and close this finding.
- **minor** `grammar-resolve-format-vs-json` (route: route:entrypoint:cmru/resolve): `resolve` selects output with its own `--format {url,env,json}` and does not offer the library `--json`, unlike worktrees, status, dependencies, standards, tool-deps and versions check. `handler wheel-validate`/`tarball-validate` (exploration) offer no machine-readable output at all. Remedy: Controller decision D5 (partial output depth) kept the converted-result subset. Follow-up: accept `--json` on resolve as the alias of `--format json` (Conflicts with --format), and give the two validate verbs `--json` through runtime.output.primary.
- **minor** `help-no-examples` (route: none): No verb declares VerbSpec.examples, so no verb help carries a pasteable example, including the verbs with more than one meaningful input (release, tester-gate, resolve --repo/--prefix, publish --build-output, cleanup modes, abandon BRANCH|PATH). The generated S-CLI.9 region and the packaged SKILL.md carry the examples today. Remedy: Add `examples=(...)` to release, publish, cleanup, abandon, tester-gate, resolve, get-py and versions resolve, copied from the skill's parse-tested example lines, then run `cli-extended surface sync`.
- **note** `adoption-doctor-cli-extended-floor-warn` (route: route:entrypoint:cmru/doctor): In an editable development venv whose stale dist metadata predates the cli-extended floor, doctor's `cli-extended` check warns instead of passing; it reads the requirement from installed cmru metadata. Remedy: Reinstall the cmru wheel (or `pip install -e` again) so dist metadata carries `cli-extended>=0.2.0`; no code change.
- **note** `help-description-style` (route: route:entrypoint:cmru/doctor): Library-built verbs (`doctor`, `skills`, `skills *`) use lowercase descriptions without a final period and no `(read-only)` mark, while cmru verbs use sentence case with a period and a (read-only) suffix on exploration verbs. `resolve` (network read) and `handler *-validate` are exploration verbs without the suffix. Remedy: Library wording is not cmru-owned; for cmru's own verbs append `(read-only)` to resolve and handler wheel-validate/tarball-validate descriptions at the next help pass. No behaviour change.
<!-- cli-extended-surface:end -->

#### Invocation roles and support policy

The installed `cmru` script is the
operator entrypoint (the `cmru-agent` and `cmru-controller` scripts were retired on
2026-10-05; see the repo-root `docs/spec-cmru-agent-controller.md`). `python -m cmru.handlers` is the one supported component
CLI because active project steps and the first-wheel bootstrap call it. The
`cmru.bundle` and `cmru.runner` modules remain Python libraries; their unused
module CLI aliases and the module alias for the operator command were
removed. `cmru run --step NAME TARGET` is the registered single-step operator
verb (the former `cmru run-step` was absorbed into `run`).

| Invocation or API | Support role | Intended use and boundary |
|---|---|---|
| `cmru` | Operator CLI | Canonical installed commands for product operations; registered verbs are the operator grammar. |
| `python -m cmru.handlers` | Bootstrap-only CLI | `build-initial-standalone.sh` uses it to build the first CMRU wheel before the installed `cmru` script exists. Project steps use `cmru handler <verb>` (the bound launcher); `cmru standards` flags the module form (BG-04/REL-07). |
| `cmru run --step` | Single-step diagnostic CLI | Preview or reproduce one declared project step in the caller's checkout with its normal project config and registered grammar. The `cmru.runner.run_step` API is also consumed by MDT (`modern-debian-tools-python-debug/build-push.py`). |
| `cmru.bundle` | Python library | Build a stack bundle from its dedicated TOML through `run_bundle`; PWMCP consumes the library. No CLI exists because no distinct operator workflow needs one. |
| `cmru.runner.run_step`, `cmru.bundle.run_bundle` | Supported Python APIs | Compose the documented component behavior from Python. `run_step` is called by MDT's `build-push.py` (guarded by an estate import test). Other module internals are not promised as public API. |
| `worktree` package | Bundled shared library API | Stable, product-neutral Git workspace primitives shipped inside the CMRU wheel; its own consumer guide and spec define the API. CMRU layers release/transaction policy on top. |
| Generated `get.py` | Standalone generated CLI | Runs without the CMRU wheel and intentionally keeps its own `argparse` parser. |

Low usage alone is not a reason to remove a component. The module CLIs for
bundle, runner, and the three operator scripts had no distinct caller use case;
the installed commands and Python APIs remain. The active handler module CLI
stays because project contracts and the first-wheel bootstrap depend on it.
The bundle and runner libraries remain independently supported.

#### Audit prompt and semantic result table

Before accepting a CLI or workflow change, reviewers MUST apply this prompt:

> **Audit every verb and option semantically.** For each verb, positional
> argument, and option, state the supported use case; accepted values and
> defaults; what omission means; selection scope; interactions and invalid
> combinations; effects on source files, local state, remote services,
> credentials, and network; dry-run and confirmation boundaries; output and
> exit-status behavior; whether the help and user documentation describe the
> actual effect; and whether an alias or legacy spelling still earns its
> compatibility cost. Review verbs and groups together: identify duplicate
> paths, missing operator use cases, hidden coupling, misleading grouping, and
> legacy surface that could be retired. Use source, generated help, behavior
> probes, the backlog, and behavioral tests as evidence. Record product choices
> as explicit decisions; do not silently preserve or change ambiguous behavior.
> Also inventory installed scripts, registered verbs, module CLIs, bootstrap
> and project-step adapters, public Python APIs, and generated standalone tools
> separately. For each, record its intended caller, wheel availability,
> corresponding shared registry or library implementation, support tier, and a
> pasteable consumer use case. Do not classify a component as bloat solely
> because no current repository invokes it; do remove no-op or redundant
> spellings that provide no distinct caller value.

#### Verb semantics not expressible in the catalog

The per-option semantics of every verb live in the reviewed catalog
`docs/cli-review.toml` (one case per spelling, choice, constraint and argument
shape, each with its `rationale`, `effects` and a grammar or early-refusal
probe; behaviour past config discovery is pinned by each verb's own tests). The table
below keeps only the VERB-LEVEL contract the catalog cannot hold: guarantees
that span several options or a whole verb, accepted product choices, and the
decisions that still block a stronger guarantee. W2-PKG5 moved the
option-level wording into the catalog rows and left each verb's cross-option
contract here, unchanged, so nothing semantic was dropped; the disposition of
every row is in `nyxloom-trove/reports/PROGRAM-2026-10-W2-PKG5-REPORT.md`.
Module-adapter rows share the registered implementation named beside them.

<!-- cmru-cli-semantic-audit:start -->
| Surface | Verb, arguments, and option semantics reviewed | Result |
|---|---|---|
| CMRU common controls | `--help` shows registered help, `--version` matches `version`, and `--log-level LEVEL` accepts the library's supported levels. `--quiet` suppresses routine output; `--debug` and `--verbose` are the same diagnostic setting; `--debug-raw` preserves raw diagnostics; `--color` and `--no-color` are mutually exclusive; `--log-prefix-time-short` timestamps severity lines. Long options require exact spelling; prefixes are not aliases. | ACCEPTED; shared controls come from cli-extended and are inherited by the installed `cmru` CLI and active handlers module adapter. |
| cmru run | Optional target uses invocation context/default project selection; `--config` selects the project or orchestration file. Repeatable `--step NAME` names declared steps, run in the order given in the CALLER'S checkout (not an isolated worktree); if none is supplied, `default_steps` remains the configured policy. A step a selected project does not declare is a usage error (exit 2) naming the declared steps (CLI-09). An empty configured default is a no-op and performs no project environment setup. This verb absorbed the former `run-step` and the hard-coded `--run-tests/--build/--push/--validate` flags. `--dry-run` resolves the same target, steps, and project-first or step-first order and prints declared cleanup, environment inputs, command argv, and command cwd without starting project commands. Dynamic `env_command` output is named but not run, so that generated value is intentionally unresolved. `--show-run-details` streams subprocess output; `--log-append` retains prior step logs. | ACCEPTED; configured defaults are preserved and made visible in help. `--remove-assets` was removed from `run`; remote cleanup has one home in `cleanup`. Project `cmru` calls use the runtime launcher bound to this invocation, with identity checked before execution (KI-11). |
| cmru worktrees | No positional target; `--json` emits machine-readable retained build/release worktree records. Default output is human-readable. This is read-only and does not require project selection. | ACCEPTED; JSON is a genuine output mode, not a mutation selector. |
| cmru dependencies | `--config` selects the orchestration file; `--json` selects machine output; `--write` updates only the marked generated graph block. `--dry-run` requires `--write` and prints the exact diff without writing; `--json` can accompany `--write` to report the same graph. | ACCEPTED; `dependency-graph` and `graph` aliases were removed to leave one canonical verb. |
| cmru build | Optional target selects projects; `--config` selects their contract. A normal build uses an isolated snapshot, runs declared prepare/gate/build steps, and retains successful local outputs addressed by source commit. `--dry-run` prints the declared plan without starting project commands. `--show-run-details` and `--log-append` control diagnostic streaming and log retention. | ACCEPTED; `--_transaction-child` is absent from user grammar. A present transaction-child marker requires complete workspace, source-root, branch, base, and workspace-ID facts and an exact shared CMRU ownership record; incomplete, recordless, or mismatched context fails closed instead of falling back to ordinary execution. The manifest records tracked and untracked source changes; any such change makes the retained output ineligible for publication (KI-10). |
| cmru publish | Optional target and `--config` select projects, each of which must declare a `push` step. Exactly one source is required: `--from-checkout` publishes whatever the caller's checkout holds (explicit, not isolated), or `--build-output ID` accepts the exact UTC timestamp plus 40-character commit ID printed by `cmru build`, requires one selected project, validates that retained record and its artifact bytes, and gives the declared push step that record as its artifact input without rebuilding. Retained output with any recorded tracked or untracked source-tree change is refused. `--dry-run` resolves and displays the record, digests, and publisher commands without requiring credentials or executing them; it does not query remote tags. `--show-run-details` streams output and `--log-append` retains logs. | ACCEPTED; missing push steps fail as a usage/configuration error before credentials or external actions. Built-in publishers require existing GitHub Releases and tags, verify the versioned tag against the recorded source commit, and update assets without creating or moving Git refs. The retained inventory stays unchanged; `release` owns source promotion (KI-10). |
| cmru changelog | Optional target selects projects; required repeatable `--backfill-tag TAG` provides one already-published tag per selected project; `--config` selects the source contract. `--dry-run` prints the exact changelog diff and creates or writes no file. | ACCEPTED; the verb is a post-release migration and refuses ambiguous tag/project matches. |
| cmru release | Optional target selects projects. `--minor`, `--major`, and `--set-version VER` are mutually exclusive and are rejected for external-version or no-tag projects; `--set-version` additionally requires exactly one selected project (CLI-11). `--dry-run` executes the declared external-version preparation only inside a disposable managed candidate so it can derive the plan; it does not gate, tag, build, publish, or promote. `--no-build` intentionally stops after tag/push. `--resume WORKTREE` resumes a retained pre-tag candidate using its recorded project scope when no target is supplied; an explicit target must match, and legacy candidates without scope metadata require an explicit target. Corrections must be committed there, then prepare and required gates rerun so the corrected commit is tagged and shipped. It is not post-tag publication recovery (KI-06). `--allow-uncommitted` permits caller edits to be omitted from the origin/main snapshot. `--allow-tag-ahead-of-head` allows only the strictly-ahead baseline case. `--allow-stale-tool-deps` relaxes freshness only; `--ahead-check-ref REF` sets the ref the local-ahead guard compares against (the snapshot is always origin/main), and `--ref` is its deprecated spelling, accepted with a warning for one release. Repeatable `--discard {logs,artifacts,evidence}` changes successful retention. Exit codes: 4 when the release plan is refused or uncommitted paths block it (nothing changed), 1 when the operation fails after starting. `--config`, `--show-run-details`, and `--log-append` select config and diagnostics. | ACCEPTED; the removed `--allow-tag-at-head` spelling has no compatibility window. Dry-run preparation is confined to the managed candidate; if that preparation fails, the retained candidate remains inspectable. KI-06 now supports corrected pre-tag resume; durable post-tag retry remains open. |
| cmru status | Optional target and `--config` choose the release view. `--minor`, `--major`, and `--set-version VER` are mutually exclusive preview selectors and are rejected for external-version/no-tag projects. `--ref REF` selects the comparison ref. `--json` emits one list with a record for EVERY selected project, changed or not (`project`, `changed`, `last_tag`, `bump`, `next_version`, `note`; `bump`/`next_version` are null when `changed` is false); the text table lists only changed projects. `status` is read-only: it takes neither `--show-run-details` nor `--log-append` and never touches `cmru.release.log` (CLI-01). | ACCEPTED; read-only status has no `--dry-run`, and it no longer accepts a hidden transaction switch. |
| cmru cleanup | Optional target selects projects. Exactly one explicit mode is REQUIRED (a bare `cleanup` is refused with the mode list, exit 2): `--policy` applies the configured `[cleanup]` policy to the resolved scope (it deletes Releases, tags and GHCR versions, runs `steps.clean`, and commits); `--remove-assets AGE` prunes configured age-based remote Releases/tags/GHCR versions estate-wide under the `[cleanup]` policy and refuses a project target (exit 2, CLI-05); `--delete-unmanaged-release-tag TAG` deletes one exact unmanaged GitHub Release but never its Git tag; `--delete-build-output ID` deletes the exact validated local retained record. Discarding a failed build worktree moved to `abandon`. `--config` selects policy. Every mutating mode displays a captured target plan and asks for confirmation; `--yes` accepts that plan without rediscovery. `--dry-run` displays the plan and performs no deletion. | ACCEPTED; explicit cleanup modes are mutually exclusive, and age cleanup applies only the targets shown before confirmation (S2.5a). |
| cmru abandon | Optional `branch` must exactly match a managed branch or the absolute path of its worktree; a release branch takes the transaction flow below, a BUILD branch or path discards that one retained failed build worktree (formerly `cleanup --discard-build-worktree`); omission selects the complete retained release set. `--config` selects the project policy used to validate each recorded scope, including externally configured multi-project releases. `--dry-run` reports exact local and known remote candidates without changing them. `--yes` confirms the complete displayed set; otherwise interactive confirmation is required. | ACCEPTED; KI-29 exact selection, publication-evidence refusal, and no-widening semantics remain canonical in S-CLI.8. |
| cmru init | `--root PATH` selects the adoption directory; `--layout` is exactly `single` or `monorepo`; `--owner`, `--repo`, and `--owner-type user or org` supply facts otherwise read from the Git origin. For `--layout single`, `--folder`, `--id`, `--description`, `--kind python or generic`, `--artifacts`, `--release-tags yes or no`, `--build-command` and `--publish-command` supply the project facts. `init` is non-interactive exactly when no required fact is open (a monorepo always asks per project); an open fact is asked through the library prompt driver, which refuses a non-terminal with exit 2. Contracts are validated with the real loaders before anything is written. `--dry-run` renders and validates without writing; the library `--yes` accepts the write confirmation, and a declined confirmation exits 0. | ACCEPTED; numeric `1`/`2` layout spellings were removed rather than carried as compatibility aliases. The per-project options deliberately avoid the removed `--project` spelling. |
| cmru versions init | Optional target and `--config` select manifests from which version targets are derived. `--dry-run` reports the prospective target changes without writing. | ACCEPTED; init remains the explicit target-creation operation. |
| cmru versions resolve | Optional target and `--config` select version resolution; normal execution writes the declared version record/native artifacts. `--dry-run` performs the read/derivation path and suppresses every declared file write. | ACCEPTED; no implicit resolution was added to build or release. |
| cmru versions check | Optional target and `--config` select the recorded target set; `--json` changes output format. Registry reads are fresh and read-only; no `--dry-run` is offered. | ACCEPTED; JSON is a real read mode, not a dry-run substitute. |
| cmru handler wheel-build; python -m cmru.handlers wheel-build | Required `--cwd` identifies the project tree. `--dry-run` validates and displays inputs without running the build. | ACCEPTED; project contracts use this explicit adapter, and the fresh-checkout bootstrap needs it before the CMRU console script exists. It is not a second parser. |
| cmru handler wheel-publish; python -m cmru.handlers wheel-publish | Required `--prefix` and `--cwd` select the release namespace and source tree; `--glob` overrides the prefix-derived asset selector; `--notes-env` names the release-notes environment variable; repeatable `--extra-asset PATH` adds uploads. `--dry-run` shows the full accepted inputs and performs no publication. | ACCEPTED; it remains a low-level declared handler; future glob/asset overlap must retain a fail-closed oracle. |
| cmru handler wheel-validate; python -m cmru.handlers wheel-validate | Required `--prefix` selects the latest release to validate. It is read-only, so no `--dry-run` is offered. | ACCEPTED; validation does not accept a meaningless mutation flag. |
| cmru handler tarball-publish; python -m cmru.handlers tarball-publish | Required `--prefix`, `--cwd`, and `--glob` select the one source; exactly one of `--version-file PATH` or `--version-env NAME` supplies its version; optional `--notes-env` supplies notes. `--dry-run` displays inputs and skips publication. | ACCEPTED; the mutually exclusive version source is semantically necessary. |
| cmru handler tarball-validate; python -m cmru.handlers tarball-validate | Required `--prefix` selects the release; `--artifact-suffix` defaults to `.tar.xz` and selects the expected extension. This is read-only and has no `--dry-run`. | ACCEPTED; a future suffix change must preserve non-empty/path-safe validation. |
| cmru handler bundle-manifest; python -m cmru.handlers bundle-manifest | Required `--name` (the project), `--tag` and `--root` (the staged bundle directory that becomes the tarball's single top-level directory) select what is described; it writes `manifest.json` there (or the file named by `--manifest-name`) with `schema_version`, `project`, `tag`, `created` and the `files` map (sha256, size, mode of every regular file), which the hardened `get.py` requires (S6.3). A symlink or special file in the tree is an error (one `[ERROR]` line, exit 1, no traceback); a `--tag` outside the installer's tag grammar is exit 2. `--dry-run` shows the inputs and writes nothing. | ACCEPTED; the producer half of the installer's refuse-unlisted-members rule, used by tarball projects such as tls-edge. |
| cmru handler oci-image-build; python -m cmru.handlers oci-image-build | Required `--cwd`, `--bake-file`, and `--bake-target` select the project build (`--bake-target` names a docker bake target, not a project target; it replaces `--target`, redesign B10). `--dry-run` previews the build without Docker login or any command. | ACCEPTED; `--repack` and the KI-02 repack tuning flags were removed (CLI-14); a test pins that `--repack` is refused as an unrecognized argument. |
| cmru handler oci-image-push; python -m cmru.handlers oci-image-push | Required `--cwd`, `--bake-file`, and `--bake-target` select the image; operation uses buildx bake push. `--dry-run` shows inputs without pushing. | ACCEPTED; no dormant repack grammar remains. |
| cmru tester-gate | Required `--cwd DIR` selects the in-container checkout path and positional `command...` is the command after `--`. `--image` selects the pinned tester image; `--cgroup-parent` overrides the required gates slice; `--forward-background-slice` and `--forward-gates-slice` control nested slice fact forwarding (renamed from `--forward-cgroup-parent-var` / `--forward-cgroup-parent-gates-var`, redesign B11); EVERY environment fallback is resolved at run time as explicit option, then the variable named as "(default: $VAR)" in the option's help, and a missing required value exits 3 (CLI-17); `--memory`, `--memory-swap`, and `--cpus` set container resource bounds. CPU must be a finite decimal of at least `0.00001` that Docker can represent; CMRU uses `--cpus` without `--cpu-period` because Docker rejects NanoCPUs and CPU Period together; it refuses zero/smaller, non-finite, or unrepresentable values before host probes because those values can remove the per-container CPU limit. `--pids-limit` (required, `CMRU_TESTER_PIDS_LIMIT`, positive integer, no default) caps the workload's process count. `--cgroup-probe-image` and `--dind-image` select helper images that run privileged, so both MUST be digest-pinned (`<repo>@sha256:<64 hex>`) and are started with `--pull=never`; `--dind-memory`, `--dind-cpus` and `--dind-pids-limit` (required with `--enable-docker`, `CMRU_TESTER_DIND_*`) bound the sidecar; `--device-read-iops`, `--device-write-iops`, `--device-read-bps`, and `--device-write-bps` set device I/O limits; `--enable-docker` requests DinD and requires its image and limits. `--dry-run` prints the workload argv and, when enabled, the DinD startup argv; it starts no container and skips privileged host slice/IO probes. Every helper and workload container receives the required `--cgroup-parent`. The workload and the DinD sidecar run under `--init` (a reaper as PID 1; the gate command is one cmru does not control and git's detached auto-maintenance orphans one process per commit). Every container has an exact name (`cmru-tester-<uuid8>`, `cmru-probe-<uuid8>`, `cmru-tester-dind-<12 hex>`); SIGTERM/SIGHUP raise `SystemExit` and `finally` runs `docker stop` then `docker rm -f` by exact name for each; a timed-out probe is removed by name. After the gate command exits, a wrapper inside the container copies the container's own `pids.events` and `memory.events` into `.cmru/tester-gate-events-<uuid>.txt` on the mounted worktree; a missing, malformed or incomplete file, a non-zero `pids.events max`, or a non-zero `memory.events oom_kill` makes the run an infrastructure failure (exit 3, naming the counter) even when the command exited 0. Otherwise the command's own exit status is preserved. Image references are validated and one starting with `-` is refused. | ACCEPTED; the DinD sizing decision is DECIDED (2026-10-05): the sidecar has separate, explicit, required memory/CPU/pids inputs, so the nested envelope is never silently shared with or doubled on top of the workload's. Live Docker refused `--memory 1g --memory-swap 512m` with status 125 before workload creation. A live `docker create` plus inspect showed `--cpus 0` left `NanoCpus`, `CpuQuota`, and `CpuPeriod` all zero; live runs showed values below `0.00001` produce `cpu.max = max 100000`, whereas `0.00001` produces a bounded quota. CMRU rejects zero, smaller, non-finite, and unrepresentable limits before privileged probes. `--dry-run` still intentionally skips host slice/IO capability checks. |
| cmru resolve | Optional target and `--config` select a project; `--format` is `json` by default or `env`/`url`. It reads the latest published artifact facts, including the selected digest, and performs no write; no `--dry-run` is offered. Config-free mode: `--repo OWNER/REPO --prefix PREFIX` resolves without reading any configuration (token from `$GITHUB_PUSH_PAT`/`$GITHUB_TOKEN`, optional for public repositories); it conflicts with `--config` and with a target (CLI-D2). | ACCEPTED; supported output formats are closed, and `--format` is an accepted exception to the library `--json` (shell consumers need `env` and `url`). The JSON shape follows the selector syntax (CLI-13): one explicit name, or the omitted current project, prints one object; `all` or a list prints a keyed map, even for one project. |
| cmru get-py | Optional target and `--config` select installer templates; `--output` writes exactly one selected installer, `--output-dir` writes one per project, and those destination options are mutually exclusive. Omission of both prints ONE project's installer to stdout; several selected projects without `--output-dir` are refused with exit 2 (CLI-12). `--dry-run` renders/validates selected templates and destinations but writes no file, and requires `--output` or `--output-dir` (stdout mode writes nothing). Generated installers intentionally keep standalone `argparse`. | ACCEPTED; the installed-wheel fix for KI-26 is in scope, and the `get` alias was removed. |
| cmru standards | Optional target and `--config` choose projects; default mode checks conformance. `--update` writes only CMRU-owned template revision markers; `--dry-run` requires `--update` and prints diffs without writing. `--json` prints one document `{schema_version, conforms, projects[{name, conforms, messages, problems}]}` on stdout (human lines move to stderr). Exit 4 means issues were found (refused by policy, nothing changed). | ACCEPTED; mixed read/write verb is `mutating` with the library `--dry-run` (D4) and a declared `Requires` of `--update`. |
| cmru tool-deps | Optional target and `--config` select declarations; `--timeout SECONDS` defaults to 10 for each network request; `--json` renders verification results; `--allow-stale-tool-deps` relaxes freshness only during verification. `--refresh PROJECT` replaces selected pins and hashes; it refuses `--json` and `--allow-stale-tool-deps` because those have no refresh meaning. `--dry-run` requires `--refresh`, fetches/verifies the proposed artifact, and prints planned file/config changes without writing. A blocking (stale or mismatched) dependency exits 4 (refused by verification policy; redesign E). | ACCEPTED; invalid combinations are declared constraints (`Requires`/`Conflicts`) that fail with status 2 rather than silently ignoring options. |
<!-- cmru-cli-semantic-audit:end -->

The verb/group review keeps distinct paths for atomic source-first release,
retained local build and artifact publication, caller-checkout publication,
configured step execution, read-only inspection, dependency/version work,
consumer resolution, and maintenance/recovery. The grammar
inventory records each cli-extended help group, and the gate compares those
groups to registration so grouping changes update this spec. Setup belongs in
`MODIFICATION`; read-only validators belong in `EXPLORATION`; conditional
read/write verbs belong in `MIXED OPERATIONS`; cleanup and abandonment belong
in `MAINTENANCE`.

The review removed duplicate names and legacy spellings (`get`,
`dependency-graph`, `graph`, numeric layout values, and `--allow-tag-at-head`),
moved remote cleanup out of `run`, and removed the hidden transaction switch
and unused repack tuning flags. KI-06 now supports committed pre-tag correction
and gated resume; durable post-tag publication recovery remains open. KI-10 now permits
publishing retained build bytes to existing Release/tag targets without creating or moving refs;
source branch promotion remains in `release`.
KI-11 binds project `cmru` calls to the invoking runtime, and KI-32 removes the
unsafe tag-only rollback override while retaining rollback. R7 keeps only the
active handlers module CLI and removes unused module launchers. The tester gate
still needs live acceptance of resource-flag combinations. These are recorded
as open work rather than described as shipped guarantees.

Every review MUST inspect confirmation and dry-run boundaries, credential
exposure, environment/default derivation, return-code and stdout/stderr
stability, use-case gaps, and compatibility cost alongside parser/help
correctness.

**S-CLI.10 — CLI grammar and semantics gate.** The CMRU gate MUST run the
grammar synchronization test in `tests/test_cli_spec_inventory.py`. A change is
not complete if a registered leaf, argument shape, option spelling (including
hidden/deprecated aliases), help group, or shared option is absent or stale.
`surface check` proves that inventory against the registry, and the guard in
`tests/test_cli_spec_inventory.py` proves the "Verb semantics" table is not
stale: every verb it names is registered, and every registered leaf has a row
or is on the test's explicit grammar-only list. None of this replaces
behavioral oracles for option effects, refusal paths, dry-run, confirmation,
output, or exit status, which live in each verb's own tests. Deliberate
refusals exit with their taxonomy code (`2`/`3`/`4`) and print one
`[ERROR] <message>` line, never an "unexpected" label; `--traceback` adds no
stack for such a refusal and still shows one for a genuine internal error.

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
value from transaction-local `cmru.vars` written by `steps.prepare`; in a dry-run, the declared
external-version prepare step runs in the disposable candidate before plan computation so that
the preview observes the same derived value. `none` leaves identity to the declared project
commands.

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
hardcodes a project's file paths or infers a step from an artifact label. The exact release tag
MUST be present on origin before CMRU invokes any build or publisher step. If a tag push reports
failure, CMRU MUST verify the origin ref: an exact match to the candidate permits continuation;
a confirmed absence MUST remove that exact local tag and stop before publication with a resumable
pre-tag candidate; an indeterminate result MUST retain the local tag and candidate for inspection.

**S-REL.4a — Prepared source is source-first and fail-closed.** A `steps.prepare` command
MAY derive a version or regenerate mechanical source inputs. Every tracked output MUST be
declared in `release.commit_generated`; cmru rejects undeclared writes, commits only declared
paths, gates that commit, and only then tags/builds/publishes; the candidate reaches main last, by a push that first merges origin/main into the candidate if main advanced (REL-04).
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

`cmru changelog P --backfill-tag <prefix><version>` is the one-time migration for a
tag that predates source-first history. It writes a generated `backfilled-after-release` entry
to the current source tree and never moves the immutable tag; the caller reviews and commits
that migration explicitly.

**S-REL.4c — Unmanaged-release cleanup.** `cmru cleanup P
--delete-unmanaged-release-tag TAG --yes` is a migration-only operation for an old GitHub
Release outside P's normal `<prefix>-v<semver>`/`<prefix>-latest` lifecycle. It requires the
explicit project namespace and confirmation (or `--dry-run`), deletes exactly one GitHub
Release with that tag, and MUST NOT delete its Git tag. A managed release is rejected; normal
project cleanup remains the sole operation allowed to delete managed Releases and tags.

**S-REL.4d — Local-build cleanup.** `cmru cleanup P --delete-build-output ID --yes`
deletes only the exact commit-addressed local build record identified by its `build.json`;
`--dry-run` is the non-mutating preview. `cmru abandon PATH --yes`
(formerly `cleanup --discard-build-worktree`) deletes only an exact, visible `cmru-build-*` (or legacy `cmru/build/*`) worktree under this repository's managed
`.worktrees/` directory. Neither operation accepts a glob, an age range, an inferred latest
record, or a release worktree. A missing, incomplete, symlinked, or unauthenticated target MUST
fail rather than widen deletion.

**S-REL.4b — Release declaration.** `[project.release]` MUST contain `git_tag` and
`build_step`. `build_step` MUST name an explicit `[steps.<name>]` command. Optional
`commit_generated = ["<project-relative path>", …]` lists mechanical tracked outputs CMRU
may commit; optional `artifact_dirs` lists publishable-output directories eligible for
artifact retention. Optional `evidence_paths` lists commit-bound files or directories
the successful release gate produces for retention. Evidence paths MUST be project-relative,
MUST contain no `..` escape, and MUST resolve without symlinks in the isolated worktree;
they are a bounded declaration, never a glob or an inferred directory.

**S-REL.5 — Reproducibility / commit model.** The isolated worktree starts clean, so wheels
cannot inherit unrelated caller dirt through setuptools-scm. cmru auto-commits **only**
declared mechanical outputs, never hand-edited source. Every project follows the same
prepare → gate → backup-push → optional tag → `build_step` → `push` → promote frame;
the project commands define what the publication phases do.
`backup-push` (S-CLI.5) is a durability step only — it pushes the candidate branch to origin
under its own name, never touching `main`; the final push to `main` (merging origin/main into the candidate if main advanced) integrates the exact
candidate commit after its public artifact succeeds. A candidate is never rebased at that point.

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

CMRU has two document grammars (select a non-default path only with `--config`). Without
an explicit path, CMRU walks ancestors to the filesystem root and selects the nearest
`cmru.orchestration.toml`; that file establishes the CMRU root and may serve several
repositories below it. A nested orchestration file starts a new root. A standalone
`cmru.toml` remains valid when it contains its own repository facts. The selected
CMRU-root secret document is the credential baseline; a selected project may explicitly
overlay it from its own folder as defined in S2.4. Secrets are never committed.

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

[runtime]
kind = "none"                      # required: none | ciu

[env]
CMRU_WHEEL_BUILDER_IMAGE = "wheel-builder@sha256:<digest>"       # required by wheel-build
CMRU_TESTER_UNIFIED_IMAGE = "tester-unified@sha256:<digest>"     # required by tester-gate
CMRU_TESTER_MEMORY = "3g"                                         # required by tester-gate
CMRU_TESTER_MEMORY_SWAP = "16g"                                   # required by tester-gate
CMRU_TESTER_CPUS = "1.5"                                          # required by tester-gate
CMRU_TESTER_PIDS_LIMIT = "4096"                                   # required by tester-gate (no default)
CMRU_TESTER_CGROUP_PROBE_IMAGE = "debian@sha256:<digest>"         # required by tester-gate; digest-pinned
CMRU_TESTER_CGROUP_PARENT = "${CGROUP_PARENT_DEV_GATES}"          # required host gates slice
# CMRU_TESTER_DIND_IMAGE = "docker@sha256:<digest>"               # required with --enable-docker; digest-pinned
# CMRU_TESTER_DIND_MEMORY = "2g"                                  # required with --enable-docker
# CMRU_TESTER_DIND_CPUS = "1.5"                                   # required with --enable-docker
# CMRU_TESTER_DIND_PIDS_LIMIT = "2048"                            # required with --enable-docker

[project]
id          = "example"           # required, lowercase project id
description = "consumer-facing product summary"
template_revision = 5              # required for `cmru standards` conformance
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
# evidence_paths = ["coverage.json"]        # only files/dirs the release gate produces
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
# manifest_pubkey  = "RWS3E3vAMFRhE+IFwPRKkv1VcLeqZIzKShZeB+QjX7u2iOMK7WfqEwk4"
#                                           # optional: 56-char minisign public key, pinned INTO get.py;
#                                           # set => signed releases are REQUIRED (S6.15). Absent => unsigned.
# launchers        = ["ciu", "cmru"]      # optional: <root>/bin/<cmd> -> ../current/venv/bin/<cmd> (S6.17)
# extensions       = ["installer/extra.py"]   # project-owned get.py command fragments (S6.14)

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

`[runtime].kind` is a closed declaration. `none` means CMRU supplies the
isolated workspace and invokes the project's declared steps without owning an
external runtime lifecycle. `ciu` means the project step is responsible for
invoking CIU from that prepared worktree and for its explicit up/health/cleanup
sequence. CMRU does not inspect Compose files or guess ownership from arbitrary
Docker commands; a missing or unknown kind is rejected before a runner starts.

**S2.2a — Central repository facts.** An orchestration document contains one
`[github]` and one `[targets]` table. Registered project documents omit those
tables; duplicates are rejected. A project document used without orchestration
keeps the tables as its standalone source of repository identity and targets.

**S2.2b — Orchestration document** (`cmru.orchestration.toml`) is the central
project registry and execution policy:

```toml
schema_version = 1
[github]
owner = "your-github-owner"
repo = "your-repository"
owner_type = "user"
[targets]
host = "github"
registry = ["ghcr.io"]
[orchestration]
project_order = ["example"]
default_steps = ["run-tests", "build", "push"]
execution_mode = "project-first"
# `default_projects` is deprecated (CLI-04): accepted with a warning, ignored, to be removed.

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
2. Deep merge CMRU-root `cmru.secret.toml` with the selected
   `<project>/cmru.secret.toml`; the nearer project table wins. The merged
   `[github].token` is the credential.

For an orchestration invocation, the root is the directory containing
`cmru.orchestration.toml`. For a portable one-project invocation, it is the directory
containing that project's `cmru.toml`. The secret grammar is strict:

```toml
# <repository-root>/cmru.secret.toml
schema_version = 1

[github]
token = "…"                         # repository-wide credential
```

```toml
# <project>/cmru.secret.toml (optional explicit override)
schema_version = 1

[github]
token = "…"
```

For compatibility, `schema_version` may be omitted from an existing secret file; when present,
it must be the integer `1`.

`[github].token` in a committed `cmru.toml` is rejected. Outside the environment and
`cmru.secret.toml` resolution above, CMRU has no fallback GitHub API credential source.
`GITHUB_PUSH_PAT`, `GITHUB_TOKEN`, and `CMRU_GIT_AUTH_TOKEN` are reserved environment names:
they MUST NOT appear as keys in project `[env]`, orchestration defaults `[env]`, or
`[steps.*.env]` tables. The strict reader MUST refuse those keys, and the resolved project
publisher token MUST be applied as protected runner environment after step settings and
environment helpers.
`tester-gate` likewise has no built-in inputs: it requires `--image`
or `CMRU_TESTER_UNIFIED_IMAGE`, plus explicit memory, combined memory/swap, CPU, pids-limit, and
host-systemd probe-image values (CLI options or the effective `[env]`). `--enable-docker` additionally
requires a nested-Docker image. `wheel-build` requires `CMRU_WHEEL_BUILDER_IMAGE`; CMRU
does not fall back to the cockpit's Python environment.

CMRU-owned GitHub transport operations (release/build source fetches, candidate and tag
pushes, promotion, and CMRU cleanup/abandon checks) use the invocation token or repository-root
`[github].token` only when `origin` is an HTTPS URL for the configured GitHub owner/repository.
The transport credential is supplied to the individual Git process through a temporary askpass
helper that refuses prompts not identifying `github.com`; it is not written to the URL, command
arguments, or persistent Git config. Credential-bearing CMRU Git calls MUST disable local hooks
because Git hooks inherit the transport process environment and could reuse the repository token
for another remote. The release gates remain the release checks. When no repository token
resolves, Git's configured authentication remains in effect. SSH and non-matching origins keep
their configured Git authentication; CMRU strips its API token environment variables from these
Git processes. The selected project's secret overlay remains scoped to that project's publisher
and does not replace the repository-level credential for CMRU's Git operations. Hook-capable
local Git operations (including commit, revert, and rebase) MUST strip `GITHUB_PUSH_PAT`,
`GITHUB_TOKEN`, and `CMRU_GIT_AUTH_TOKEN` from the Git child environment so local hooks can run
without receiving a CMRU publisher credential. The local ancestry probes performed by
`cmru abandon` MUST use the same credential-stripping helper.

### S2.6a — tester-gate environment preflight (KI-17)

These values are normally supplied by
`cmru.orchestration.toml [env]` and reach a step through `cmru release`; they are NOT usually
set in the project's own `cmru.toml [env]`. So a step copied out of `cmru.toml` and run by hand
(what an operator does when a release goes red) would otherwise fail one missing variable at a
time, each costing a container spin-up, and each message would send the reader to the wrong
file. `tester-gate` MUST therefore, before any actual-launch resolver with a side effect
(the slice-existence probe, the container launch): (1) validate the full required set at once — image, memory,
memory/swap, CPU, pids limit, probe image, gates slice, and the nested-Docker image and its
memory/CPU/pids limits when `--enable-docker` — resolving
each at `explicit flag > environment` precedence, and abort naming EVERY still-missing variable
together; and (2) in that report and in each individual resolver's message, name
`cmru.orchestration.toml [env]` (inherited through `cmru release`) as the real source. This is
the same required set `cmru standards` validates statically against a project's declared config
(one shared constant, `REQUIRED_TESTER_ENV`), including the required
`CMRU_TESTER_CGROUP_PARENT` gates binding. Direct CLI use must pass that binding
or an explicit `--cgroup-parent`; an unscoped launch is refused. CMRU MUST pass the resolved
CPU limit through Docker's `--cpus` option and MUST NOT also set `--cpu-period`, because Docker
rejects `NanoCPUs` and `CpuPeriod` in the same HostConfig. Before the privileged slice or IO
probe, the resolved CPU limit MUST be a finite value of at least `0.00001` that Docker can
represent as a nonzero per-container quota; this check applies equally to `--cpus` and the
environment value.

`--dry-run` is the deliberate exception to host probes: it prints the resolved workload
argv and, with `--enable-docker`, the DinD startup argv, but starts no container. The
slice and IO-support checks themselves require temporary privileged containers, so dry-run
prints a note that those host checks were skipped. An actual launch runs them first. The
slice probe, IO probe, DinD sidecar, and workload all receive the resolved gates
`--cgroup-parent`; no helper container may fall through to Docker's default tier.

If none is found and a write verb is invoked, cmru MUST exit 3 (V10).

**S2.5 — Cleanup selectors are explicit.** In `cmru.orchestration.toml`, an
empty `cleanup.release_tag_prefixes` or `cleanup.ghcr_packages` list selects
nothing. Only an explicit `"*"` selects every release or package. CMRU MUST
never reinterpret an empty destructive selector as a wildcard.

**S2.5a — Cleanup confirmation applies the previewed target set.** Every
mutating `cmru cleanup` invocation MUST enumerate and render its target set
before confirmation. It MUST apply that captured set after confirmation, not
rediscover targets and widen the action. Age-based cleanup MUST compute its
cutoff once for the plan; an object that appears or crosses the cutoff while a
prompt is open is not part of the confirmed set. CMRU MUST use stable release
and package-version IDs where available, and revalidate exact local build
records/worktree identities before deleting them. For every local or remote Git
tag, the plan MUST capture the ref's exact object ID and delete it only if that
ref still points to the captured object when the plan is applied. A ref absent
from the preview MUST remain outside the plan; a ref created or retargeted while
confirmation is open MUST be skipped. Local deletion MUST pass the captured ID
as the expected old value to `git update-ref`, and remote deletion MUST use a
force-with-lease for that exact tag ref. Before deleting a GitHub Release, CMRU
MUST re-fetch the captured release ID and require its tag, update time, asset
inventory, and displayed eligibility to match the preview. Before deleting a
GHCR package version, it MUST re-fetch the captured version ID and require its
update time and container-tag inventory to match the preview and still satisfy
the age policy. A successful `git ls-remote --tags` response MUST consist only
of well-formed object-ID/ref records whose tag names are valid Git refs and
match the requested pattern; CMRU MUST reject the complete response if any row
is malformed, duplicated, or outside the pattern. It MAY discard a peeled
`refs/tags/<name>^{}` row only after validating its object ID and requiring the
corresponding ordinary tag ref in the same response. A malformed or incomplete
inventory MUST fail before cleanup applies deletions. A changed or indeterminate
record MUST be skipped or refused without deleting it. For a declared `steps.clean`, CMRU
MUST snapshot dirty paths immediately before the step and stage and commit only
literal paths that become dirty during it; paths already dirty at that point
MUST remain outside the cleanup commit. It MUST commit such paths even
when the cleanup policy selected no release tags for deletion. `--dry-run`
renders the same plan and performs no mutation; `--yes` confirms that same
captured plan.

**S2.6 — Tool dependencies** (`[[project.tool_dependencies]]`, S15). A project
declares a first-party artifact its OWN tests/tooling consume (not a released
product output — see S1). This is distinct from
`[orchestration.project.<id>].depends_on`, which is release ORDER. Internal
vbpub consumers of Assay use selected-worktree source mode and need no S15
entry; this table is for an explicitly vendored external/copy artifact.

```toml
[[project.tool_dependencies]]
project = "assay"                          # required: a first-party project in this estate
version = "1.0.0"                          # required: the pinned version
path    = "tools/assay/assay-1.0.0.pyz"    # required: project-relative path to the vendored artifact
sha256  = "6224f784f96f5ad9d10264a69dd69594639959c5eda847dcede822a7adc515bf"  # required: lowercase hex digest of the vendored bytes
```

Validated strictly, like the rest of S2 (exit 2 on any violation): unknown keys
are rejected; `project`, `version`, `path`, `sha256` are all required
non-empty strings; `project` MUST be a lowercase project identifier and MUST
NOT equal the declaring project's own `id` (a project may not declare a tool
dependency on itself); `path` MUST be project-relative and MUST NOT escape the
project root (no absolute path, no `..` segment); `sha256` MUST be exactly 64
lowercase hex characters. Whether `project` names a REAL sibling project in
this estate cannot be checked from one project document alone (a project
document stays portable to a fresh repository root, S2's own design
constraint) — that cross-project check is performed by `cmru.dependencies`'
`build_report`, alongside the analogous check for `depends_on` (S15.1).

**S2.7 — Version policy and generated state** (`[versions]`). Both
`cmru.orchestration.toml` and `cmru.toml` MAY declare `[versions]` with a positive
`age_window_days` (default 14), `targets`, optional `outputs`, and optional `discovery`. Unknown keys and source
families are rejected (exit 2). Target source tables use the closed values `.pypi`, `.npm`,
`.go`, and `.oci`; `mode` is exactly `single` or `aligned`. Constraints use the documented
common SemVer-compatible subset. A single target has exactly one source; an aligned target has
at least two and CMRU MUST find the same version in every source whose timestamp is at or before
the target's cutoff. OCI source tables name a fully-qualified image. Their `selection` field is
`selection = "semver"` by default or `selection = "rolling"`. SemVer selection uses a tag template with exactly one
`{version}` placeholder; every other tag character is literal. Rolling selection uses one
literal OCI tag, requires `mode = "single"` and `constraint = "*"`, and MUST NOT be aligned with
other sources or use an exact version override. CMRU MUST record a valid `Docker-Content-Digest`
sha256 digest for a rolling tag, and `check` MUST treat a digest change as a refresh even when
the tag string is unchanged.
When an OCI index contains Docker attestation descriptors marked
`vnd.docker.reference.type = "attestation-manifest"`, CMRU MUST exclude those descriptors from
runtime-platform age calculation. Every remaining runtime platform MUST have a usable registry or
publisher-created timestamp; missing evidence MUST fail closed. This keeps build provenance and
SBOM metadata manifests from being treated as deployable image variants.

Each registry source table MUST name only environment-variable references (`token_env` or the
complete `username_env`/`password_env` pair) for credentials; it MUST NOT contain credential
values or embedded-credential URLs. Missing referenced credentials are unavailable prerequisites
(exit 3). CMRU MUST NOT forward credentials to an OCI bearer-token realm outside the configured
registry origin, except Docker Hub's documented `auth.docker.io` token host.

A target's exact `version` override MUST carry a non-empty `reason`. If its version is newer
than the age cutoff in any configured source, it MUST carry a future ISO-date `expires`; CMRU
refuses an expired override. Otherwise CMRU selects the highest SemVer-compatible, age-eligible
version. The cutoff is `resolve time - age_window_days`, and releases exactly at the cutoff are
eligible for every source family, including literal rolling OCI tags. When an age-window refusal
is determined by Go `.info` commit time or publisher-created OCI time, the refusal diagnostic MUST
include the corresponding weaker-evidence warning.

Root targets and their `resolved` state belong to `cmru.orchestration.toml` and use the root age
window. A project target declaration or overlay is project-local: it uses that project's
effective age window and stores `resolved` in that project's `cmru.toml`. A project age-window
setting alone MUST NOT alter a root target. Deep table values merge recursively; scalars and
arrays replace.

`[versions.discovery]` MAY set `scope` to `shipped` or `all`. Omitted `scope` means `shipped`.
The orchestration root MAY set the shared scope default; `pypi_extras` and `requirements_files`
are project-local fields and MUST be declared in a project's `cmru.toml`. `pypi_extras` is an
array of names from `pyproject.toml [project.optional-dependencies]`; it is valid only with
`scope = "shipped"`. `requirements_files` is an array of project-relative `.in` or `.txt` files.

With `scope = "shipped"`, `cmru versions init` derives supported dependency facts from
`requirements.in` and configured `requirements_files`, `pyproject.toml` project dependencies
and selected extras, npm `dependencies`/`optionalDependencies`/`peerDependencies`, and Go
`go.mod` require entries (including `// indirect` entries). With `scope = "all"`, it also reads
all Python optional extras, PEP 735 dependency groups, `build-system.requires`, conventional
root `requirements*.in`/`requirements*.txt` files and files under `requirements/`, and npm
`devDependencies`. Skipped non-registry, unsupported-syntax, and unsupported-source entries
MUST be reported. `cmru versions resolve` writes the
generated result plus native outputs: Python constraints, npm direct versions/overrides and
lockfile, Go `go.mod`/`go.sum`, OCI JSON, and any configured strict Jinja2 output. `cmru versions
check` performs a fresh read-only comparison. None of these actions is coupled to build, release,
gates, or scheduling.

Timestamp evidence is recorded per source. PyPI uses upload time; npm uses registry version
time; Go uses module proxy `.info` `Time`, which is VCS commit time rather than proxy publication
time, and MUST emit a warning; OCI uses HTTP `Last-Modified` when present and may fall back to the
publisher-provided `org.opencontainers.image.created` annotation or image config `created`, also
with a warning. A missing usable timestamp MUST fail closed. Native Python constraints apply
uv's age cutoff to the transitive resolution; npm's lock update applies `--before` and the
package-specific `--min-release-age-exclude` for explicit overrides/looser project cutoffs. Go
CMRU age-checks each declared Go module target against proxy `.info` metadata before `go get`
writes its native files. Because Go proxy `@v/list` omits pseudo-versions, CMRU also checks a
pseudo-version named by the target constraint and requests `@latest` when no listed version
satisfies that constraint. If `.info` and `@latest` return the same version with different commit
times, CMRU MUST refuse the inconsistent age evidence. In active workspace mode, `go get` may
update `go.work` and `go.work.sum`; resolve MUST snapshot those files and restore them if any later
writer fails.

The built-in OCI output is JSON schema 1 at `versions/oci-images-YYYYMMDD.json` and the stable
`versions/oci-images.json`; existing files may be replaced only when their `generated_by` marker
is `cmru versions resolve`. Rolling OCI entries include the recorded `digest`. A configured
`[versions.outputs.<id>]` requires `template`, `path`,
and `dated_path` (with `{date}`), all project-relative. It renders with Jinja2 `StrictUndefined`
and is an optional feature requiring the `versions-templates` extra. The context contains
`resolved_at` and `targets`, whose entries contain `version`, `override`, `reason`, `expires`,
`owner`, `age_cutoff`, and per-source `version`, `released_at`, `age_source`, and optional `tag`.

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
MUST insert `\n---\n` before the new record. Native `cmru release` overwrites
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
artifacts, and generated gate evidence for inspection/resume. Successful release MUST remove
the worktree, but MUST retain its project logs, declared artifacts, and declared gate evidence
by default before doing so: logs move to `<project>/logs/cmru-release/<immutable-id>/`, declared
`project.release.artifact_dirs` move to `<project>/artifacts/<immutable-id>/` with the existing
`release.json` source-commit and SHA-256 inventory, and declared `project.release.evidence_paths`
move to `<project>/evidence/cmru-release/<immutable-id>/`. The evidence coordinate contains
the declared files/directories and an `evidence.json` manifest recording the source commit and
SHA-256 hash/byte inventory. `release --discard logs`, `--discard artifacts`,
and `--discard evidence` are explicit independent opt-outs. A project declaring no
`artifact_dirs` or no `evidence_paths` has nothing to retain for that half and is skipped
without error — retention applies uniformly across every orchestrated project, not all of
which build a local artifact or produce commit-bound evidence. `cmru build` MUST use an isolated
`cmru-build-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>` worktree (S-CLI.5b). On child success it MUST copy that project's logs to
`<project>/logs/<commit-date>_<full-commit>/` and every declared
`project.release.artifact_dirs` directory to
`<project>/artifacts/<commit-date>_<full-commit>/`, write a `build.json` SHA-256
inventory, then remove the worktree. The coordinate is the built HEAD's UTC commit timestamp
and full SHA; an existing coordinate is an error, never an overwrite. `build.json` MUST record
the source identity, artifact and log inventories, and any tracked or untracked source-tree
changes. Any non-empty `source_tree_changes` makes the record ineligible for publication; CMRU
MUST refuse real and dry-run `publish --build-output` for it. Projects MUST ignore their expected
generated build outputs so those outputs do not appear as source-tree changes, without ignoring
source files or using ignore rules to conceal source edits. Ignore rules do not hide modifications
to already-tracked files; a build that modifies tracked source or generated files remains ineligible.
A clean record is eligible for explicit
`cmru publish --build-output ID` only after CMRU revalidates the complete inventory; it is not a
source release candidate. A child or retention failure MUST retain the
worktree and print its exact path for debugging; use `cmru worktrees` to discover it and the
exact cleanup verb after inspection.

---

## S4 — Publication boundary

**S4.1** An actual `cmru publish` requires a resolved publication credential (exit 3 when absent),
then runs the selected project's explicit `push` step through the unified runner. A dry-run
does not require credentials. It does not
discover an artifact, choose a host, or infer a release-asset policy.

Without `--build-output`, `publish` runs the declared push step against the caller worktree.
With `--build-output ID`, it validates the selected build record and supplies its exact
inventoried bytes to the project's push step without rebuilding. Built-in wheel and tarball
handlers consume the retained inventory directly; a custom publisher MUST consume files beneath
`CMRU_BUILD_OUTPUT_ROOT`, verify any staged copy against `build.json` before upload, and MUST NOT
rebuild from the caller worktree or create/move Git refs.
Before making any remote request, built-in handlers MUST hash each staged upload and compare its
size and digest with the corresponding `build.json` entry; a mismatch MUST refuse publication.
Publication MUST refuse if the manifest records any tracked or untracked source-tree change,
including during `--dry-run`; expected generated outputs therefore need appropriate ignore rules.
The built-in handlers require existing versioned and `<prefix>-latest` GitHub Releases and tags;
the versioned tag MUST resolve to the retained source commit. They update Release assets in place,
without creating Release records or Git refs. A dry-run verifies the local record but does not
query remote tags. The retained record is not modified while the handlers generate sidecars and
`latest.json`; those files are created from temporary copies. When the retained stable version is
older than the highest current version, publication MUST leave `<prefix>-latest` unchanged. It MUST
recheck the highest version immediately before updating the pointer, to avoid moving it backward
when another release appears during the asset upload. This path publishes artifact bytes
without source branch promotion; the source-first tagged workflow remains `cmru release`. See
[KI-10](../KNOWN_ISSUES_TODO_BACKLOG.md#ki-10--publish-retained-build-output-by-id--shipped).

**S4.2** A project command that publishes a GitHub Release asset MUST create a `.sha256`
sidecar in `sha256sum -c` format and bind the release tag to the build commit. A retained-build
publisher MUST refuse an absent or mismatched version tag instead of creating or moving one. It
SHOULD include the artifact digest in release notes; an OCI publisher SHOULD also verify the final
registry manifest digest.

**S4.3** A project that maintains a `<prefix>latest/latest.json` pointer MUST update it in
the same explicit push contract. `cmru resolve` can consume that pointer, but CMRU does not
invent one for a project that did not choose it. Retained-build publication updates an existing
pointer Release in place and MUST refuse if its GitHub Release or tag is absent; it does not
delete/recreate the pointer tag. For a stable retained version older than the highest current
version, it MUST leave the pointer unchanged and recheck the highest version immediately before
any pointer upload.

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

**S5.1** `cmru resolve <name>` returns `{version, tag, asset, sha256, url}` for the highest-semver release matching `prefix`.

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

**S6.1** `cmru get-py <name> --config cmru.toml` emits a standalone Python 3
installer to stdout. The output is a rendering of the packaged
`cmru/templates/get.py.tmpl` resource (resolved with `importlib.resources`) with
`[[VARNAME]]` placeholders replaced from the `[installer]` config. The rendering is
deterministic (byte-identical for identical config) from both a source checkout and an
installed wheel. The CMRU wheel MUST include the template resource and `cli-extended` runtime
package. The gate MUST build/install that wheel into an isolated environment, invoke its
`cmru get-py` console script from outside the source checkout, and compile the emitted
installer. Any unreplaced `[[...]]` placeholder is a render error (exit 2), never a warning.
Rendering is a single `[[NAME]]` pass; every value that lands in code goes through
`json.dumps` (a bad value, such as a `"` in the GitHub owner, is refused with exit 2), values
that land in docstring/message text must be plain names, and the installer fields have a
grammar: absolute `install_dir_system`, relative `install_dir_user`/`entrypoint`/`preserve`
without `..`, `asset_suffix == ".tar.xz"`, plain file names for `manifest_name`/`signature_name`,
plain command names for `required_commands`/`launchers`, and a 56-character base64
`manifest_pubkey`.

**S6.2** Commands emitted:

```
get.py install  [--config HOST.toml] [--version TAG] [--scope system|user] [--variant NAME]
get.py update   [--version TAG] [--scope system|user] [--variant NAME]
get.py status   [--scope system|user]
get.py rollback [--version TAG] [--scope system|user]
```

`--config FILE` is installed as `<root>/shared/host.toml` (mode 0600, written atomically) once
the release is live, and is handed to the adapter as `--config` during the transaction;
without it the adapter gets `<root>/shared/host.toml`. There is no `--manifest-pubkey` flag:
the key is pinned in the rendered `get.py` (S6.15). `status` is read-only: it reconciles a
`state.json` that is missing or behind `current` in memory and never writes it (the next
install/update/rollback does). Render-time validation refuses an `install_dir_system` that is
not an absolute normalised path (no `//`, `.`, trailing `/`) of at least two components, so `/`,
`/opt`, `/srv`, `/tmp`, `/root`, `/usr`, `/etc`, `/bin`, `/sbin`, `/lib`, `/var`, `/boot` and
`/home` are refused (a path below one, such as `/usr/local/<name>` or `/opt/<name>`, is fine), and a `preserve` or `install_dir_user` entry that
normalises to the root itself (`.`, `a/..`).

**S6.3** Transactional pipeline (install / update):

The pipeline is **fail-closed** (S6.15): every verification failure is exit 1 and leaves
`<root>` as it was.

1. **Pre-flight** — `required_commands`, `python3-venv` (when wheels are configured) and,
   when a `manifest_pubkey` is configured, the `minisign` binary, ALL before any network I/O
   (exit 3 if missing). System scope needs root (exit 3).
2. **Lock + read state** — `flock` on `<root>/.lock` (opened `O_NOFOLLOW`); as root,
   `<root>`, `releases`, `shared` and `bin` must be real directories (not symlinks) owned by
   the effective uid and not group/world-writable, and are created that way (`0755`), else
   exit 1 with nothing written through them; read `state.json`; delete leftover `.incomplete`
   release dirs (crash recovery; never for a pre-W1 install, whose old releases survive until
   the new release is live).
3. **Resolve** — `--version X` installs exactly X (no "latest" lookup happens). Without it the
   highest-semver `TAG_PREFIX*` release is resolved and the resolved tag is printed. The tag
   must be `<prefix><version>` made of `[A-Za-z0-9._+-]` (exit 2 otherwise). Re-running the
   current version and variant is a no-op that re-verifies the recorded manifest digest (and
   still applies `--config` and rewrites the launchers). `--version ""` is exit 2, never
   "latest". An unpinned `update` whose latest release is OLDER than the installed one is
   refused (exit 1; the message says to pass `--version`); an explicit older `--version` is
   allowed and prints a downgrade notice.
4. **Download + SHA256** — fetch `<tag><asset_suffix>` + its `.sha256` sidecar (for a
   multi-variant release `<tag>-<variant><asset_suffix>`, S6.12). The sidecar must be exactly
   `<64 hex>[  <asset name>]`; a mismatch is exit 1, before extraction. HTTPS only, on every
   redirect hop (host allowlist); the token is never forwarded across a redirect.
5. **Read + verify the manifest** — the bundle must have exactly one top-level directory;
   `manifest_name` (and `signature_name`) are read straight from it into memory. When a
   `manifest_pubkey` is configured the signature must verify (S6.15). The manifest must parse,
   have `schema_version` 1 and, when it carries `tag`/`version`, they must equal the requested
   tag. These in-memory bytes are the ONLY manifest used afterwards.
6. **Build the release** at its final path `<root>/releases/<tag>-<manifest12>[-<variant>]/`,
   marked `.incomplete`: extract the bundle to `tree/` (`filter="data"` where the interpreter
   has `tarfile.data_filter`, otherwise setuid/setgid/group-write stripped and the archive's
   owner ignored; plus a pre-scan that refuses the bundle on absolute paths, `..`, device
   nodes, absolute/escaping links, and any non-directory member that is neither a key of
   manifest `files` nor a wheel matching a configured wheel glob; a symlink or hardlink is
   allowed only when its target is a listed file; the manifest and signature are not
   extracted), write the verified `manifest.json` (+ `.minisig`), check every manifest `files`
   entry (the adapter MUST be listed, or the install is refused; a `preserve` path that this
   installer replaced with a link into `<root>/shared` is the operator's copy and is not
   hashed), link `preserve` paths, then install wheels (S6.16). Resource bounds, checked
   before anything is written: download at most 512 MiB, declared extracted size at most
   2 GiB, at most 50,000 archive members; a bundle beyond any of them is refused (exit 1).
7. **Invoke adapter** (`bootstrap` on install, `apply` on update/migration) — if `entrypoint`
   is set. Non-zero exit aborts before the swap.
8. **Commit** — write `release.json`, then `.complete` (last), then atomically swap `current`,
   then write `state.json` atomically. Any failure before the swap deletes the new release dir.
9. **After the swap** — install `--config`, write launchers (S6.17), persist the variant,
   prune: only `current` and `previous` are kept.

**S6.4** Release layout:

```
<root>/releases/<tag>-<manifest12>[-<variant>]/
    tree/            # extracted bundle (the adapter's --release-root)
    venv/            # this release's own interpreter (venvs are not relocatable)
    wheelhouse/ requirements.lock    # the verified wheels and their hash lock
    manifest.json [manifest.json.minisig]   # exactly the verified bytes
    release.json     # {name, tag, variant, manifest_sha256}
    .complete        # written last; `.incomplete` while building
<root>/current        # symlink -> releases/<name>  (atomic swap)
<root>/state.json     # {schema, current, previous, history}  (atomic write)
<root>/bin/<cmd>      # launchers -> ../current/venv/bin/<cmd>
<root>/shared/        # preserved config/state (host.toml, .variant); never inside releases/
```

`<root>` = `install_dir_system` (system scope) or `$XDG_DATA_HOME/<install_dir_user>` /
`~/.local/share/<install_dir_user>` (user scope). If the process dies between the symlink swap
and the state write, the next run reconciles `state.json` from the `current` target's own
`release.json`. A corrupt `state.json` or a dangling `current` is refused (exit 1).

**Pre-W1 layout (migration).** A host installed by the older template (`current` symlink, no
`state.json`, content directly in `releases/<tag>/`, one shared `<root>/venv`) is **migrated
by the next `install` or `update`**: the new release is built beside the old one, `current`
is swapped atomically, and only then are the old release dirs and the shared `<root>/venv`
removed. The legacy release becomes neither `previous` nor history (it has no per-release venv
or digest), so the first migrated host has no rollback target until its next update;
`rollback` says so and exits 1. A failed migration leaves the legacy install untouched and
working. `status` marks such a host `pre-W1 layout`.

**S6.5** Preserve: files in `installer.preserve` are copied from the live release's `tree/` to
`<root>/shared/` after the new bundle has verified, and symlinked back into the new `tree/`.
They survive across updates and rollbacks.

**S6.6** Rollback: `get.py rollback` goes to `state.previous`, which must be `.complete` and
is re-verified (recorded manifest digest, signature when a key is configured, adapter and
`files` hashes) before the adapter runs `action=rollback` and `current` is swapped, using
that release's own venv. `state.previous` becomes the release rolled away from, so a second
rollback toggles back. `--version TAG` selects a recorded release of that tag from `previous`
or the history, if its directory still exists (pruning keeps only `current` and `previous`).
When there is no `previous` (a fresh install, or a host just migrated from the pre-W1 layout)
`rollback` exits 1 with: "the pre-migration layout is not a rollback target; the first update
after migration creates one".

**S6.7** Scope-exclusive lock (`flock` on `<root>/.lock`) serialises concurrent invocations.
SIGINT/SIGTERM handler cleans up staging dir and releases the lock.

**S6.8** Adapter invocation contract (Seam 1):

```
<release>/venv/bin/python <release>/tree/<entrypoint> <action> \
    --release-root <release>/tree \
    --config <root>/shared/host.toml \
    --manifest <release>/manifest.json
```

(`<release>` = `<root>/releases/<name>`; without wheels there is no venv and the adapter runs
under the installer's own interpreter.)

`<action>` ∈ `{bootstrap, apply, health, rollback}`. Non-zero adapter exit → exit 1.
The GitHub token is **stripped** from the child-process environment.

**S6.9** The installer is Python 3 **stdlib-only** (urllib/tarfile/hashlib/argparse/fcntl);
no third-party dependencies. The project adapter, `python3 -m venv`/`pip` (wheels, S6.16) and,
only when a `manifest_pubkey` is configured, `minisign` (S6.15) are shelled out; any other
command in `required_commands` is only presence-checked.

**S6.10** Auth (token) precedence: `--github-token` (warns: leaks via ps/history) >
`--github-token-file FILE` (rejected if loose perms / wrong owner) > `--github-token-stdin`
> `CMRU_GITHUB_TOKEN` / `GITHUB_TOKEN` env. Token is never logged in full.

**S6.11** `install_dir_user` degrades gracefully: if `entrypoint` is empty and `wheels`
is empty, no adapter is called and no venv is created (tls-edge minimal path).

**S6.13** `--version <TAG>` pins the install to a specific tag (bare semver or full tag) and
installs EXACTLY that tag: no "latest" lookup is made, so a moved or newer release can never
substitute for it (and an unreachable release list does not block a pinned install). Without
`--version` the highest-semver release is resolved. Arguments go to the right side of the pipe
(`curl … | sudo python3 - install --version …`), so there is no env-var-across-pipe footgun.

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

**S6.14** **Installer extensions (cmru program 2026-10, O4).** The generic installer
(`install`/`update`/`status`/`rollback`) is the whole of what cmru ships. Project-specific
commands (for example ciu's host `enroll`) are **project-owned fragments** inlined into the
single rendered file:

- Config: `[project.installer] extensions = ["<relpath>.py", ...]` (optional list). Each path
  is project-relative, must not be absolute or contain `..`, must end in `.py`, and must be
  unique (config error, exit 2). The file must exist at render time and, after resolving
  symlinks, lie inside the project directory (render error, exit 2).
- Render: the template's `# @@EXTENSIONS@@` marker line (after every core helper and `do_*`
  function, before `check_prerequisites`/`main`) is replaced by each fragment's bytes
  **verbatim**, in declared order, wrapped in
  `# --- extension: <relpath> sha256=<hex of the fragment file bytes> ---` and
  `# --- end extension: <relpath> ---`. With no extensions the marker line is removed and the
  output is a plain installer with no extension code. `[[VARNAME]]` placeholders are replaced
  over the whole result, fragments included (the banner digest covers the raw file bytes).
  The output stays ONE file with one digest and is byte-identical across renders.
- Render-time checks (`ast`; each refusal names the fragment and a line): (a) the fragment
  parses; (b) its top-level names collide with neither the template's top-level names nor
  another fragment's (imports are exempt from fragment-to-fragment collisions, but an import
  that rebinds a template name collides); (c) every load of a template top-level name that no
  fragment scope shadows is in `EXTENSION_API`; (d) imports are stdlib only
  (`sys.stdlib_module_names`; relative imports refused); (e) at least one top-level
  `_EXTENSIONS.append(<name>)`.
  Check (b) also covers every module-scope binding (inside top-level `if`/`try`/`for`/`with`/
  `match`, `del` targets, match captures), refuses star imports, and refuses `global`/`nonlocal`
  statements naming a template name; check (c) also walks argument and return annotations.
  **These checks are a contract/lint guard over repo-owned fragments, not a security boundary:**
  `globals()`, `getattr`, `exec` and similar dynamic access remain possible, and fragments are
  trusted code reviewed with the repository.
- Runtime: `_EXTENSIONS` is a list of `register(subparsers) -> {command: handler}`. After the
  core subparsers are added, `main()` calls each registered function in order and merges the
  returned dicts. A command that duplicates a core command or another extension's (including an
  argparse duplicate-subparser error) is `fatal`, exit 2; a returned handler with no subparser
  of that name is also exit 2. Dispatch is `handler(args, token)`, the same shape as the core
  `do_*` functions; the token is the resolved GitHub token.
- **`EXTENSION_API` is a stability contract.** The rendered file carries a tuple
  `EXTENSION_API` naming every template top-level name a fragment may use (today:
  `EXIT_CONFIG`, `EXIT_FAIL`, `EXIT_PREREQ`, `_EXTENSIONS`, `_c`, `_current_version`,
  `_root_dir`, `do_install`, `fatal`, `hr`, `info`, `ok`, `warn`). Renaming, changing the
  signature of, or removing a name requires updating every in-repo fragment in the same change;
  adding a name is deliberate and made together with the fragment that needs it. Fragments
  carry their own stdlib imports; the template's imports are not part of the API.
- Consequence: a project that renders `get.py` without `extensions` carries no root-run
  `authorized_keys` writer. Host enrollment is ciu's (`ciu/installer/enroll.py`); its hardening
  is tracked in ciu (CIU-122/CIU-123), not here.

**S6.15** Signature policy, fail-closed behaviour and exit codes.

- *Unsigned projects.* With no `manifest_pubkey` the releases are unsigned; `install` says so
  ("unsigned"), `status` shows `signed: no`, and `minisign` is not required.
- *Signed projects.* With `manifest_pubkey` the key is pinned into the rendered `get.py`
  (never a flag or environment variable). `minisign` and the venv prerequisites are checked
  BEFORE any network I/O (exit 3). The bundle MUST contain `signature_name` next to the
  manifest; the signature is verified over the exact manifest bytes with
  `minisign -V -m <manifest> -x <sig> -P <key>`; a missing or invalid signature, or one from
  another key, is exit 1 and changes nothing under `<root>`. Rollback and the idempotent
  re-install re-verify it.
- *Trusted-comment binding (replay protection).* The signed trusted comment MUST be exactly
  `project=<name> tag=<tag> manifest_sha256=<hex>`: `<tag>` is the tag being installed and
  `<hex>` the SHA-256 of the manifest bytes. The installer refuses (exit 1) when either field
  differs, so an old signed release cannot be replayed as another tag and a signature cannot
  be moved onto a different manifest. Producer primitives that emit exactly this:
  `cmru.manifest.build_trusted_comment(project, tag, manifest_path)` and
  `cmru.delegated.minisign_sign(blob, secret_key, trusted_comment)` (writes
  `<blob>.minisig`); a test signs with them and installs the result. **Gap:** nothing in the
  `cmru release` pipeline calls them yet (GETPY-REDESIGN R4, automatic signing at release); a
  project that sets `manifest_pubkey` must sign from a project-owned release step with those
  primitives until R4 lands, otherwise every install of its releases fails closed.
- *Fail-closed.* Every verification failure (checksum, sidecar grammar, signature, manifest
  schema/tag, `files` hashes, wheel hashes, adapter not covered by the manifest, unsafe tar
  member) is exit 1 with the install untouched.
- *Exit codes:* `0` ok (including a verified no-op); `1` download/verify/install/adapter
  failure; `2` configuration or render error (bad tag or variant, missing `--config` file,
  invalid installer field); `3` missing prerequisite (command, `minisign`, `python3-venv`, root
  for system scope).
- *Transport.* HTTPS only, every redirect hop checked against the host allowlist
  (`api.github.com`, `github.com`, `uploads.github.com`, `objects.githubusercontent.com`,
  `*.githubusercontent.com`, `*.github.com`); the token is never forwarded across a redirect; an empty token on stdin is
  exit 2; a non-200 asset download is fatal.

**S6.16** Wheels, offline installs, hash lock. When `[[project.installer.wheels]]` is set, each
wheel glob must match exactly one file in the bundle and have a manifest entry
`manifest[<distribution>] = {sha256, wheel?, size?}` that it matches (a missing or mismatching
entry is exit 1). The verified wheels are copied to `<release>/wheelhouse/` and a
`requirements.lock` of `<dist>==<version> --hash=sha256:<hex>` lines is written, with
`cli-extended` first and the rest in declared order. The release's own venv
(`python3 -m venv`, so the `python3-venv` package is a prerequisite, exit 3) is populated with
`pip install --isolated --no-index --find-links <wheelhouse> --require-hashes -r
requirements.lock`, then `pip check`: no index, no network, no resolver choice, and any wheel
swapped after verification fails the hash lock. pip also runs with `PIP_CONFIG_FILE=/dev/null`
and no `PIP_*` variables from the caller: `--isolated` alone leaves pip's global config
(`/etc/pip.conf`, `$XDG_CONFIG_DIRS`) active, and a global `target`/`prefix` could redirect
the install. The wheel version that goes into the lock must be a plain version string.

**S6.17** Launchers. `launchers = ["a", "b"]` (plain command names) makes the installer create
`<root>/bin/<cmd>` symlinks to `../current/venv/bin/<cmd>`, rewritten atomically after every
install, update and rollback so they follow `current`. Add `<root>/bin` to `PATH`.

---

## S7 — External third-party tool integration (not yet config-enabled)

CMRU does **not** accept a `[project.delegated]` table. Earlier documentation
advertised one even though no release lifecycle invoked it; the strict schema rejects it.
No missing-tool path may silently skip a requested security or packaging operation.

Candidate integrations and their required contracts are recorded in KI-04. Before one is
added, it MUST have an explicit artifact/digest input, an output location and publication
rule, a fail-closed prerequisite policy, provenance binding, and an end-to-end release test.
The source-first `CHANGES.md` transaction record remains the CMRU-native release history;
`git-cliff` is not a replacement for it.

This section is about tooling from OUTSIDE the estate (e.g. `git-cliff`), which remains
unimplemented pending those contracts. It is distinct from S15's `[[project.tool_dependencies]]`
— a FIRST-PARTY artifact produced and released by another project already inside this same
estate (e.g. cmru's own vendored `assay` zipapp) — which is a config feature and IS
implemented, precisely because it can reuse the estate's own release/publish/digest machinery
instead of inventing a new provenance story for an external tool.

---

## S8 — Exit Codes

cmru uses a five-value exit code scheme. Codes 0-3 carry the same meanings as CIU S10.3
(`0` success, `1` runtime failure, `2` configuration/validation error, `3` environment
prerequisite missing); cmru adds `4`, which CIU does not have (CIU S10.3 stops at `3`, and its
`2` also covers argparse usage errors, as cmru's does). The scheme is therefore a superset of, not
identical to, CIU S10.3 (redesign section E, CLI-16):

| Code | Meaning |
|---|---|
| `0` | Done: success, a declined confirmation, or nothing to do. Declining exits `0` in every verb that confirms: `init` (the write), `abandon` (the abandon, and the retained-build-worktree discard path of `abandon`), and `cleanup` (the pending-action list) |
| `1` | The operation failed after it started: build/publish failure or native version-artifact writer failure |
| `2` | Usage or configuration error (missing required field, unknown key, parse error, bad argument) |
| `3` | Missing prerequisite, including an unavailable registry metadata source, required environment variable, external tool, a `tester-gate` missing its required configuration, and a cmru that is not installed as a distribution |
| `4` | Refused by policy or verification; nothing was changed |

A refusal made before anything changed exits `4` in EVERY verb that can refuse; `1` is reserved
for failure after the operation started. The refusal is one exception type
(`transaction.RefusedBeforeChange`, the held lock being `transaction.ReleaseLockHeld`), mapped to
`4` by the verb dispatch, never recognised by message text.

Verbs that exit `4` (root and delegates):

- `release`: the release plan is refused (S12.2a/S12.2b, including stale tool-deps at the plan
  stage), uncommitted paths in a selected project block the run, or another release transaction
  already holds the release lock;
- `build`: uncommitted paths in a selected project block the build (it snapshots `origin/main`),
  or another release transaction already holds the release lock;
- `abandon`: ambiguous or published transactions block it (also on `--dry-run`), origin state
  changed on the post-confirmation re-check, or another release transaction already holds the lock;
- `standards`: any standards issue is reported (delegate);
- `tool-deps`: stale or blocking tool declarations without `--allow-stale-tool-deps` (delegate).

No other verb exits `4`.

**S8.1 Process environment owned by cmru's own plumbing** (redesign section D):

- `CMRU_RELEASE_LOG` is an OPERATOR input: the path of the aggregate `release` log (default
  `<repo root>/cmru.release.log`). It is read only by `cmru release`.
- `CMRU_NATIVE_RELEASE_LOGGING=0` is a TEST-ONLY switch that disables the release tee (it
  re-points the process's file descriptors, which an in-process test must not do). It is not an
  operator control and has no CLI spelling.
- `CMRU_INTERNAL_BIN`, `CMRU_INTERNAL_RUN_LOG`, `CMRU_INTERNAL_SHOW_RUN_DETAILS`,
  `CMRU_INTERNAL_LOG_APPEND` and `CMRU_INTERNAL_LOG_PREFIX_TIME_SHORT` are INTERNAL hand-offs
  from a cmru process to its own children. They are not operator inputs and no operator-facing
  document may tell anyone to set them (the operator spellings are the `--show-run-details`,
  `--log-append` and `--log-prefix-time-short` flags and `CMRU_RELEASE_LOG`). The old
  un-prefixed spellings (`CMRU_BIN`, `CMRU_RUN_LOG`, `CMRU_SHOW_RUN_DETAILS`, `CMRU_LOG_APPEND`,
  `CMRU_LOG_PREFIX_TIME_SHORT`) are ignored. `CMRU_INTERNAL_BIN` is the one that selects an
  executable, so it is honoured ONLY inside a verified transaction child
  (`transaction.internal_launcher`); everywhere else the launcher is `cmru` resolved from `PATH`.
  The other four only change presentation and logging and are set by the same process that reads
  them (the flags above), so they are not child-gated.

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

**S9.4a — Bundle configuration** `cmru.bundle.run_bundle(CONFIG_PATH)` MUST read this component configuration. `cmru.bundle` is a Python library; no `python -m cmru.bundle` command is supported. Paths may be absolute; relative paths use the bases below. The schema is closed: unknown keys at the root or in any table MUST be rejected, and values MUST have the declared TOML types. Paths, strings, and string arrays MUST be non-empty where required; the loader MUST NOT coerce strings, booleans, or array members from another type.

| Key | Type | Requirement and meaning |
|---|---|---|
| `project_root` | path | Required. Relative to the directory containing the config. It is the base for copied source paths and for the default wheel project root. |
| `dist_dir` | path | Optional; defaults to `dist`. Relative to `project_root`. The builder removes this whole tree before a real build. |
| `bundle_dir` | path | Optional; defaults to `bundle`. Relative to `dist_dir`; this is the assembled archive root. |
| `client_dir` | path | Optional; defaults to `client`. Relative to `dist_dir`; enabled wheel builds place client wheels here. |
| `[wheel].enabled` | boolean | Optional; defaults to `false`. Enables `python -m pip wheel`. |
| `[wheel].python_bin` | string | Optional; defaults to `python3`. Executable used for the wheel build. |
| `[wheel].project_root` | path | Optional; defaults to `project_root`. A relative value is based on the config directory. |
| `[wheel].find_links` | path | Optional. Declares the local wheelhouse as the ONLY package source of the wheel build (`pip wheel . --no-index --find-links DIR`; it must therefore also contain the build requirements and every dependency, so a public index can never supply an estate-internal name); a relative value is based on the config directory. Omitted, pip uses its default index. |
| `[archive].name_template` | string | Required. Filename template containing exactly one plain `{version}` field and no path separators; it is replaced with the value of the named environment variable. The resulting filename is checked again after substitution. |
| `[archive].version_env` | string | Required. Name of the environment variable that must be set for preview and build. |
| `[archive].format` | string | Optional; defaults to `gztar`. Allowed values: `tar`, `gztar`, `bztar`, `xztar`, `zip`. The deterministic normalized writer is guaranteed for `xztar`. |
| `[copy].files` | array of strings | Within the required `[copy]` table; defaults to an empty list when omitted. File paths are resolved from `project_root`. |
| `[copy].dirs` | array of strings | Within the required `[copy]` table; defaults to an empty list when omitted. Directory paths are resolved from `project_root`. |

For example, a project with no client wheel can start with:

```toml
project_root = "."

[archive]
name_template = "example-{version}.tar.xz"
version_env = "EXAMPLE_VERSION"
format = "xztar"

[copy]
files = ["README.md"]
dirs = ["src"]
```

Set `EXAMPLE_VERSION` before calling `run_bundle()`; the library validates the
configuration and then performs the build, including removing `dist_dir` before
assembling output. The template must contain one plain `{version}` field; other
placeholders, format conversions, and path separators are refused. The rendered
filename is also refused if the environment value introduces a path separator.
There is no bundle CLI or library dry-run API. Consumers
that need a review step should inspect their declared source inputs before
calling the build function. Unknown keys and wrong TOML types fail at the
configuration boundary (KI-33).

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
| V23 | `[[project.tool_dependencies]]` entries have all four required non-empty string keys (`project`, `version`, `path`, `sha256`); unknown keys are rejected (S2.6) | 2 |
| V24 | `[[project.tool_dependencies]].project` is a lowercase project identifier and MUST NOT equal the declaring project's own `id` | 2 |
| V25 | `[[project.tool_dependencies]].path` is project-relative and MUST NOT escape the project root | 2 |
| V26 | `[[project.tool_dependencies]].sha256` is exactly 64 lowercase hex characters | 2 |
| V27 | `[[project.tool_dependencies]].project` names a real first-party project in the loaded estate (cross-project check, `cmru.dependencies.build_report`, S15.1) | 2 |
| V28 | A tool dependency (S15) whose integrity, authenticity, or (absent `--allow-stale-tool-deps`) freshness check fails MUST refuse `cmru release`; `cmru tool-deps` exits the same way on demand | 2 |
| V29 | `[versions]` keys, modes, source tables, timestamp state, and exact-override requirements validate strictly (S2.7) | 2 |

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

**S12.2** Change detection: a project is eligible for release iff `git log <last_tag>..HEAD -- <paths>` succeeds and is non-empty after excluding CMRU release-control files and generated release-history documents. If no prior tag exists, the project is always eligible (first release). A nonzero history-query result MUST refuse release planning with Git's diagnostic; it MUST NOT be interpreted as an empty range or an unchanged project.

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
   this one — back to an ordinary skip, for the deliberate case. The former
   `--allow-tag-at-head` alias was removed; the supported spelling is
   `--allow-tag-ahead-of-head`.
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
worktree.** No project's `prepare`/gate/tag cycle has started when the plan itself
refuses — nothing was gated, promoted, or tagged, and the durability backup branch (S-CLI.5a)
was never pushed either, since it is pushed only after the plan is accepted. The isolated
release transaction MUST therefore surface this as a clean operator-facing `[ERROR]` message
(never a raw Python traceback) and discard the just-created worktree/branch exactly like a
successful release would — never retain it the way a genuine mid-release failure is retained
for inspection (S-CLI.1), since there would be nothing there to inspect. This exit is `4`
("refused by policy; nothing changed", S8), distinct from `1` ("failed after starting"). The
transaction also records the refusal as its own state (alongside the existing scope marker,
S-CLI.5a) so the parent process can tell "refused before starting" apart from "failed after
starting" from the recorded state, not only from the code.

**S12.2e — The isolated release transaction's unchanged/skipped path MUST name the exact
baseline and reason, per project, never a bare project-name list (KI-13).** Before this rule,
the plan printed the baseline tag only on the CHANGED path (`assay: assay-v2.0.0 →
assay-v2.1.0 (minor)`) and withheld it on the UNCHANGED path (`Unchanged, skipping: ciu, cmru,
assay, …`) — exactly backwards: the changed case never needed disambiguation, and the unchanged
case is precisely where an operator who just committed under a project's own path cannot tell a
wrong `paths` glob, a misplaced/unpushed tag (S12.2a/S12.2b's refusals), and a genuinely
unchanged project apart. With `check_tag_at_head` True (the isolated release transaction; never
`cmru status`/`cmru changelog`, which keep S12.2's plain silent skip), every one of the three
S12.2b states that is not itself a refusal MUST print exactly one `[INFO] Unchanged, skipping:
<name> (…)` line, sharing one message shape rather than a competing style per state:

* **"equal"** (S12.2b.1): `<name> (already released as <tag> at the snapshot commit; nothing
  new since)`.
* **"ahead"**, downgraded via `--allow-tag-ahead-of-head` (S12.2b.2's abort, deliberately
  skipped instead): `<name> (tag <tag> is ahead of the snapshot commit; skipped via
  --allow-tag-ahead-of-head)`.
* **"behind"** (S12.2b.3, the ordinary case and by far the most common — some other project's
  commits moved HEAD, this project's own paths didn't change): `<name> (no commits under
  <path>/[, <path>/…] since <tag> @ <short-sha>)`, naming every watched path (S12.3) and the
  baseline tag's own resolved commit, e.g. `assay (no commits under assay/ since assay-v2.1.0 @
  52534ef7)`.

A project with no prior tag remains always eligible (first release, S12.2) — it is never
reported as unchanged, and this rule prints nothing for it. One line per project keeps this
readable even at cmru's real scale (seven products): a per-project line each, never a merged
wall of duplicated prose, and never the old bare `Unchanged, skipping: a, b, c` list once each
skipped project has already printed its own line above.

**S-CLI.5c — `--dry-run` MUST NOT be the only way to learn something about a real run
(KI-14).** Before plan computation, a dry run MAY execute each selected project's declared
external-version `steps.prepare` in the disposable candidate, because `external:VAR` cannot be
observed without its declared source query. This phase commits only declared generated outputs
inside that candidate and performs no gate, tag, build, push, or promotion. The isolated release
transaction then computes its plan (S12.2a/S12.2b, S12.2e) exactly once, unconditionally,
before branching on `--dry-run` — both paths therefore observe identical
decision-level diagnostics (the plan summary, the baseline, the derived version, and every
per-project unchanged reason), differing only in the `[DRY] Would …` prefix on what a real run
instead performs for real, and in the absence of that run's effects. `--dry-run` MAY show
strictly less than a real run (a real run's own operational/progress output has no dry-run
analogue, since there is nothing happening to report), but MUST NEVER show a decision an
operator could not also learn by watching a real run to completion.

**S-CLI.5d — The origin durability backup branch (S-CLI.5a) MUST be deleted only when THIS
transaction actually pushed it, tracked as transaction state (KI-15).** A dry run, a "nothing
to release" run, and a refused release plan (S12.2d) all reach a successful exit without ever
calling the push that creates this backup. Attempting its deletion unconditionally on those
paths is what made a genuinely successful, side-effect-free run print `error: unable to delete
'…': remote ref does not exist` / `error: failed to push some refs to '…'` — indistinguishable
from a real failure, and training operators to read this release tool's `error:` output as
noise. The transaction MUST instead record, as its own state, whether its own push of this
branch succeeded, and check that record before ever attempting a delete. When it DID push one,
the delete MUST still always be attempted — never skipped merely because it might fail, since
treating a genuine push as "nothing to clean up" is the worse failure: it orphans a real branch
on origin forever, silently, which is far more expensive to notice than a stray log line. Any
delete that is attempted remains best-effort (a release that already succeeded MUST NOT fail,
or appear to fail, over cleanup of a branch whose job is already done) but MUST NOT surface at
`error:` level even on a genuine failure.

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
| `external:VAR` | Read VAR from `<cwd>/cmru.vars` after prepare; in `--dry-run`, run only the declared external-version prepare first; tag HEAD | Prepare commit, if changed |

**S12.6** Dev builds: when HEAD is untagged, the version MUST be `X.Y.Z.devN+g<hash>`. These MUST NOT produce a `<prefix>-v` tag or immutable release.

**S12.7** `cmru release` MUST use the isolated transaction in S-CLI.5. It rejects dirty
release inputs, not unrelated caller paths; the child worktree itself MUST remain clean
except for declared generated paths at their permitted lifecycle point.

**S12.8** Commit/tag ordering: for `file` strategy — write VERSION, stage, commit, then tag. For `scm`/`counter` — tag HEAD directly. In all cases: tag first, then build, then publish.

---

## S14 — Explicit OCI Command Library

`cmru handler oci-image-build --cwd . --bake-file docker-bake.hcl --bake-target all`
and `oci-image-push` are optional commands a project may place in its required build and
push steps. CMRU never selects them automatically and has no `[project.oci]` table.

The normal helper path verifies Docker/Buildx, uses Docker's native credential store, then
runs `docker buildx bake -f <bake-file> <target> --load` or `--push`. A publishing
transaction preflights its GitHub credential before source or host state changes; a handler
also refuses missing Docker prerequisites with exit 3.

`--repack` was removed from the handler grammar (CLI-14): it only ever failed closed while
KI-02 is open, which S-CLI.7 treats as dead grammar. The option returns when KI-02 is fixed.
OCI repack is not a supported CMRU release feature. A project that needs OCI repacking
MUST own an explicit tested command and its reproducibility, resource-governance, digest
verification, and runtime-smoke evidence. MDT is the estate example; its implementation is
not silently generalized as a CMRU profile.

---

## S15 — Tool Dependencies (declaration + verification)

A cmru-managed project's OWN tests/tooling may consume a first-party artifact
produced and released by ANOTHER project in the same estate. Internal vbpub
consumers use selected-worktree source mode for Assay, so their run-gate lane
has no artifact to declare here. S15 remains the explicit, first-class fact
and three-check verification for a consumer that deliberately vendors a copy
at a release boundary.

**S15.1 — Declaration** (`[[project.tool_dependencies]]`, S2.6). Each entry
names the provider `project`, the pinned `version`, the vendored artifact's
project-relative `path`, and its recorded `sha256`. Structural validation
(unknown keys, required non-empty fields, path safety, digest shape,
no-self-declaration) happens per-document in `cmru.config` (S2.6); whether
`project` names a real sibling project in THIS estate is a cross-project
check, performed once every project document is loaded, by
`cmru.dependencies.build_report` — the same module, and the same "declared vs.
actual" comparison discipline, that already reconciles `depends_on` against
first-party wheel inputs (S1). A tool dependency is reported there as a THIRD
edge `kind` (`"tool"`, alongside `"declared"` and `"artifact"`).

**S15.2 — A tool edge is reported but MUST NEVER be validated against
`project_order`.** `depends_on` edges (`"declared"`) and first-party wheel
edges (`"artifact"`) are both checked against `project_order` — the provider
MUST release before the consumer. Routing a `"tool"` edge through that SAME
check would make cmru→assay a cycle against assay→cmru (S15's own opening
paragraph) and refuse to load a config that is not actually broken. This
exclusion is deliberate and permanent, not an oversight to "complete" later —
`cmru.dependencies.build_report`'s tool-edge loop carries an explicit comment
saying so, precisely so a future change does not silently reintroduce the
cycle by generalizing the ordering check across all edge kinds.

**S15.3 — Three checks, kept distinct in code and in every message.** A
declared tool dependency is verified along three INDEPENDENT axes; a
verification MUST never conflate one for another, and every reported line
names exactly which one it is:

1. **Integrity** — do the vendored bytes match the recorded `sha256`? Purely
   local, never touches the network, always resolvable (pass/fail only). This
   is what a project's own `sha256sum -c *.sha256` test step already proves
   (S9); S15 makes it an explicit, machine-readable fact in the same model as
   the other two checks, rather than a fact that exists only inside one
   project's shell command.
2. **Authenticity** — does that recorded hash equal the digest of the
   PUBLISHED release asset, for that project and EXACT pinned version? This is
   the name-versus-object check for artifacts (the same shape as S12.2a's tag
   name-versus-object check, and for the identical underlying reason a prior
   defect there was found and fixed): a file named `assay-1.0.0.pyz` is not
   thereby assay 1.0.0. The published asset's bytes are downloaded and hashed;
   the filename is used only to locate WHICH asset to download, never as
   evidence of authenticity in itself, and the recorded/local digest is never
   compared against a version string.
3. **Freshness** — is the pinned version the HIGHEST released version for that
   project's tag prefix? This is the staleness check, and it is orthogonal to
   authenticity: a pin can be simultaneously authentic (genuinely is what it
   claims to be) AND stale (a newer real release exists it does not yet use).

Authenticity and freshness both require the published-release catalog for the
provider project — network state that is legitimately absent (S15.4) or
unreachable (S15.5). Integrity has neither state; it is always resolvable
without any network access.

**S15.4 — Bootstrap: no release exists yet.** A fresh estate with nothing
released on `origin` MUST still build, and MUST NOT report a corrupt or
mismatched vendored artifact merely because nothing has been published yet to
compare it against. When the provider project's tag prefix has zero published
releases, authenticity and freshness are both reported as `unresolved`
(`reason = "no-release"`) — a THIRD, explicit outcome, distinct from both
`pass` and `fail`. Integrity still runs and still reports pass/fail normally.

**S15.4a — Bootstrap MUST be established by a genuinely empty release list,
never by a repository the check could not see.** Measured against GitHub's
real API: a repository with genuinely zero releases returns HTTP 200 with an
empty `[]` body; a repository that is missing, renamed, private, or
misspelled in `[github]` returns HTTP 404 on that same listing endpoint. A 404
on the releases-LIST call therefore MUST NEVER be read as S15.4's bootstrap
case — it MUST raise the S15.5 network/inaccessible outcome instead. Treating
list-404 as bootstrap would let a repository that becomes inaccessible stop
being checked silently and permanently, reported as the benign "could not
check" outcome forever, while authenticity/freshness verification has in
truth stopped happening entirely — a real, standing mismatch condition wearing
S15.3's "could not check" outcome, which S15.3–S15.5 exist specifically to
prevent. (A 404 for one EXACT tag, e.g. `/releases/tags/<tag>`, is a different,
legitimate, directly-checked fact — "this specific version was never
published" — once the release list itself has already been read successfully;
S15.4a is about the LIST endpoint only.)

**S15.5 — Network unavailable is its own distinct outcome, and MUST NOT
hang.** Every GitHub request S15 verification makes carries an explicit
timeout (`cmru tool-deps --timeout`, default 10s). A connect/DNS/timeout
failure, or an unexpected HTTP status, is reported as `unresolved` (`reason =
"network-error"`) for authenticity and/or freshness — never silently folded
into a passing result (a hidden mismatch would defeat the entire feature) and
never reported as a failure (an operator's flaky network is not evidence of a
corrupted or inauthentic artifact). A genuine hash mismatch or a corrupted
local file MUST NEVER be reported as merely "could not check" (S15.3's
distinction exists precisely so this cannot happen), and "could not check"
MUST NEVER be reported as success.

**S15.6 — Verification runs at release time and on demand; NEVER during
tests.** `cmru tool-deps [P[,P...]] [--json] [--allow-stale-tool-deps]
[--refresh PROVIDER_PROJECT] [--timeout S]` runs all three checks and reports
per dependency (read-only; `--refresh` is the one exception, S15.8). The same
verification is wired into the isolated release transaction's plan-computation
phase (S12.2a/S12.2b's own network-touching preflight, before any project's
prepare/gate cycle starts), scoped to exactly the projects this run
will release — an unrelated orchestrated project's stale or unreachable tool
dependency MUST NOT block a run that never touches it, and a no-op run (no
project changed) makes zero network calls for this check. It runs identically
for `--dry-run`, for the same reason S-CLI.5c requires the tag preflight to
(a preview and a real run report identical decision-level diagnostics).

Verification MUST NEVER run as a side effect of `cmru tester-gate` / `pytest` /
any part of the test suite. This is not a performance optimization — it is the
entire reason a pinned artifact is vendored rather than fetched: the test
suite MUST stay hermetic, reproducible, and bootstrappable from a bare clone
with no network access at all. A project's `run-tests` step MAY still run its
OWN local integrity check (e.g. `sha256sum -c`, S15.3's first check) — that is
local-only and always was safe; only the network-touching
authenticity/freshness checks are excluded from the test path.

**S15.6a — Cross-project verification REQUIRES `cmru.orchestration.toml`; a
single-project invocation refuses rather than misreport.** Authenticity and
freshness resolve a dependency's PROVIDER project's own tag `prefix` (S15.1),
which requires seeing that sibling project — impossible from a single
`<project>/cmru.toml` load (S2's own portability rule: a project document
stays runnable in a fresh repository root with no visibility into a former
monorepo's siblings). `cmru tool-deps` (and `--refresh`) MUST detect this
up front — any selected project that declares a tool dependency, loaded from
anything other than `cmru.orchestration.toml` — and refuse the INVOCATION
with a message naming the missing estate-wide context, before any check runs.
It MUST NOT fall through to `verify_project`'s "provider not found" outcome:
that outcome is an authenticity FAILURE with no override, so a documented,
perfectly ordinary single-project invocation of a genuinely authentic pin
would otherwise hard-fail for a reason that has nothing to do with the
artifact.

**S15.7 — A stale or mismatched tool dependency is an ERROR by default.** An
integrity failure (corrupted local bytes) or an authenticity failure (the
recorded hash does not match the published object, or the pinned version was
never published at all) MUST block a release, with NO override — these are
objective evidence of a bad artifact. A freshness failure (stale: a newer real
release exists) MUST also block a release by default, but MAY be overridden
with `--allow-stale-tool-deps` — staleness is a policy judgement an operator
may deliberately accept, not a corruption signal. `unresolved` outcomes
(S15.4, S15.5) never block: "could not check" is not evidence of a problem.

**S15.8 — Refresh is explicit and NEVER automatic.** `cmru tool-deps --refresh
PROVIDER_PROJECT` re-vendors the declared artifact from PROVIDER_PROJECT's
latest published release: it downloads the new asset, writes it under the
declaring project's own tree (renaming to embed the new version, removing the
old file), and rewrites exactly that one `[[project.tool_dependencies]]`
entry's `version`/`path`/`sha256` in `cmru.toml` — a marked, surgical text edit
that leaves the rest of a hand-formatted document untouched (the same
discipline as `cmru dependencies --write`'s generated comment block and `cmru
standards --update`'s revision-marker edit). No verification path, and no
release, ever calls this on its own; it is a deliberate, separate operator
action, reviewed like any other source change before it is committed.

---

## S16 — Release-gate rigor (Assay-backed)

CMRU's internal `assay.toml` declares the `cmru` lane at R0/R1/R3. R0 runs
the full CMRU test suite. R1 judges the `coverage.json` artifact against
the highest-version previously published ancestor CMRU release tag (currently `cmru-v5.5.0`), requires 100%
line and branch coverage, and forbids excluded source. After release N is
tagged, advance this pin to N on the next release candidate; the candidate
for N remains pinned to N-1 so a tagged-HEAD rerun excludes its tag and finds
the same baseline in HEAD's full ancestry. `main` is not a stable
post-merge baseline: Assay's merge-base can resolve it to the tested commit,
leaving no changed lines to measure. R3 runs an import-break canary against
`src/cmru/config.py`.
`--maxfail=1` is inert on the passing baseline, so it does not shorten a green
full-suite run.

The release `gate` supplies R2 separately through `run-gate.toml`'s
`mutation` lane. A release candidate is already at `origin/main`; using
`main` as Assay's mutation base would leave no changed-source candidates and
correctly produce `NO_MUTANTS`. The dedicated lane selects the highest-version
published `cmru-v*` tag in the candidate's full ancestry and requires it to
match the configured Assay R1 base in `assay.toml`, then mutates CMRU source
changed since that tag. The host gate sends all published CMRU tag names and
commit IDs to the tester so the checker can verify the selection through
Assay's sanitized ancestry API. On an untagged candidate, the selected ancestor
MUST also be the latest published CMRU release. On a tagged-HEAD rerun, the
checker excludes every local CMRU release tag at HEAD before selecting the
highest-version published ancestor as the baseline; the latest published tag
may be one of the verified tags at HEAD. Equal-distance tags on different
merge parents are ordered by release version rather than Git's `describe`
traversal choice.
The registered host `gate` lane queries
origin with CMRU's credential-scoped Git transport immediately before the
mutation lane and passes only token-free facts for every published tag and its
commit through
`CMRU_ASSAY_BASELINE_FACTS` to its dedicated `cmru-mutation` environment. The
guard binds those facts to HEAD, verifies the selected tag's origin commit, and
checks every local CMRU release tag at HEAD against its exact origin commit,
including older tag names that point to the same commit. It also checks Assay's
effective comparison commit.
If Assay's merge first parent differs from the tag commit, the configured CMRU
source roots MUST be unchanged between them.
A missing tag or mismatched base fails the gate. If HEAD itself is
release-tagged during a rerun, the selected baseline MUST be the highest-version
published tag in HEAD's full ancestry after excluding every CMRU release tag at
HEAD.
An empty source diff writes skip evidence bound to HEAD. The serial campaign
has a 120-second per-candidate timeout,
`--maxfail=1`, `--resume`, and a progress stream.
`run-gate.py gate` runs the Assay R0/R1/R3 lane plus the tag-based R2 campaign,
total coverage, and the cause-sensitive canary.

**S16.1 — Snapshot boundary.** Assay R1/R3 run in
`repository-minus-unsafe-symlinks`, with exactly the three tracked Topos
fixture paths listed in `cmru/assay.toml` omitted because their absolute link
targets cannot be materialised safely. A new unsafe symlink is a gate error;
the omission list is not a general exclusion mechanism.

**S16.2 — Gate ownership and evidence.** The `assay` lane in
`run-gate.toml` installs Assay from the selected vbpub worktree and invokes
the `cmru` lane with the mandatory resume/progress arguments. Its verdict is
`.assay/verdict-cmru.json` and its progress stream is
`.assay/progress-cmru.jsonl`, both outside the judged tree. The separate R2
mutation lane records `.assay/mutation-cmru.json` and appends
`.assay/progress-mutation-cmru.jsonl`; the JSON record contains mutation outcomes and the JSONL
file is an append-only progress stream. Its first-failure limit stops a bad mutant from running
the rest of the suite. Mutation outcomes MAY be reused on resume only when the test suite and
copied fixture closure exactly match the recorded run; any test or fixture change requires a
new campaign. Both campaigns preserve their own resume state. The mutation and canary controls copy the CMRU test closure,
including `topos/cmru.toml` and `nyxloom/cmru.toml`, which the estate adoption
contract test reads. Missing closure files fail the control before mutation or
canary evidence is written. The `gate` lane runs these with the total-coverage
and cause-sensitive canary checks as one release contract.

**S16.3 — Admission boundary.** The canonical entrypoint is
`./run-gate.py`; the tester-unified lane requires the estate-provided
`$CGROUP_PARENT_DEV_GATES` slice and fails closed when it is absent or not a
loaded unit. A local devcontainer pytest result is not gate evidence.

**S16.4 — Publisher credential boundary.** Before starting any tester-unified
lane, the registered host `gate` lane MUST resolve the selected CMRU Git auth,
save all visible root/project `cmru.secret.toml` overlays in a private
temporary directory outside the repository mount, and replace their worktree
paths with symlinks to those host-only backups. Every nested `run-gate.py`
process MUST have
`GITHUB_PUSH_PAT`, `GITHUB_TOKEN`, `CMRU_GIT_AUTH_TOKEN`, and
`RUN_GATE_EXTRA_MOUNTS` removed. The host MAY use its in-memory auth object for
scoped origin queries; only token-free tag and commit facts may be forwarded
to `cmru-mutation`. The host MUST restore the original overlay bytes, owner,
group, mode, and timestamps after success or failure. A restore failure MUST
fail the gate and MUST retain the private backup directory, reporting its path,
until restoration succeeds. Copying an overlay into a retained worktree MUST open the source
without following symlinks, reject symlink and nonregular destination paths, and install the
mode-0600 copy atomically from a sibling temporary file.
Direct tester component lanes do not apply this host wrapper and MUST only run
when the mounted checkout contains no publisher secret overlays.

**S16.5 — Real enrollment (moved to ciu).** cmru no longer ships host enrollment:
`get.py enroll` is ciu-owned code inlined through the installer `extensions`
mechanism (S6.14), and its real-system container oracles run in ciu's own
`enroll` lane (`ciu/run-gate.toml`, `CIU_ENROLL_REQUIRED=1`). cmru's `gate` has no
enrollment lane.

---

## S13 — Reserved / Out of Scope

The following are explicitly **out of scope** for cmru v1 and MUST NOT be implemented:

- macOS/Windows code signing (Authenticode, Apple notarization).
- FTP/SFTP deploy targets (e.g., netcup `deploy.zip`). These are deploy operations, not releases.
- New release hosts beyond GitHub v1 (fast-follow, via S11 interface only).
- Adding an external supply-chain tool without the artifact/digest, output, prerequisite,
  provenance, and end-to-end-release contracts required by S7. The source-first release
  history required by S-REL.4a is CMRU's own transaction record.
