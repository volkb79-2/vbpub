# cmru — Configurable Multi Release Utility

One release CLI for a **central registry of independently-versioned products** that share a
**single** GitHub Releases page. cmru gives each product its own `<prefix><semver>` tag line and a monorepo-safe per-product "latest" (GitHub's repo-global *Latest* badge can only point at one release; cmru's resolver fixes that).

cmru is **just the orchestrator**: it owns the generic git/host mechanics (tags, commits, GitHub Releases, ghcr pruning, the `latest.json` pointer) and calls each project's own `build`/`push`/`clean` step commands for the artifact-specific work. No project logic is hardcoded in cmru.

## Install

```bash
pip install -e .             # provides the `cmru` console script
```

CMRU's local tag inspection requires Git 2.43 or newer. Check `git --version`
before running release or cleanup workflows; the reason is in the
[local tag inspection design](docs/DESIGN-GUIDE.md#local-tag-inspection-requires-git-243).
The [consumer guide](docs/CONSUMERS.md#git-version-for-local-tag-inspection)
shows the prerequisite check.

The wheel also installs the companion `cmru-agent` and `cmru-controller`
entrypoints. All three use CMRU's registered CLI grammar; `cmru --help` lists
root verbs, and `cmru help <verb>` (or `<verb> --help`) shows that verb's exact
options. The old release-scoped `--abandon` switch is removed; use
`cmru abandon [BRANCH]` for a retained release transaction.

The installed `cmru` executable is portable: run it from a project directory,
repository, or CMRU root, or pass `--config /path/to/cmru.toml`. Without an
explicit path it searches ancestors to the filesystem root for the nearest
`cmru.orchestration.toml`; that file establishes the CMRU root and may serve
several repositories below it. A nested orchestration file starts a new root.

To build CMRU itself before any CMRU wheel is installed, use the supported
fresh-checkout bootstrap script. It imports handlers from `src`; the wheel bytes
are built in the dedicated `wheel-builder` image, so the host does not need the
`build` package:

```bash
cd /workspaces/vbpub/cmru
./build-initial-standalone.sh
```

The image is defined by [`wheel-builder/Dockerfile`](../wheel-builder/Dockerfile).
The script prints the manual virtual-environment install commands after it produces
the wheel; once installed, all subsequent builds use the `cmru` console script.

The wheel installs the operator commands `cmru`, `cmru-agent`, and
`cmru-controller`. It also carries the supported `python -m cmru.handlers`
project-step and bootstrap CLI, the `cmru.bundle` and `cmru.runner` Python libraries, and the
`cli-extended` and `worktree` libraries they use. Use installed console scripts
for operator commands; the retired module CLI aliases for bundle, runner, and
the operator scripts refuse and direct callers to the supported interface. See the
[design rationale](docs/DESIGN-GUIDE.md#one-declared-cli-grammar) and the
[consumer adoption guide](docs/CONSUMERS.md#using-the-wheel-and-component-interfaces).

## The model: declared outputs and explicit behavior

Each project declares its released output vocabulary in `artifacts = [...]` (`wheel`,
`oci-image`, `tarball`, or `bundle`). That is a machine-readable inventory for release
history and retained artifacts—not a profile that injects build, Docker, or publishing
behavior. Every action is an explicit project step.

`[project.version].strategy` determines version discovery. `[project.release].git_tag`
separately states whether CMRU mints and pushes an annotated Git tag. `build_step` names
the step which makes retained outputs. `commit_generated` lists the only mechanical tracked
outputs CMRU may commit before its gate. This permits a GitHub wheel release, an image-only
registry publication, or a combined image+bundle release without hidden behavior.

Every project also declares its runtime owner explicitly:

In `[runtime]`, set `kind = "none"` or `kind = "ciu"`. `none`
means the project command owns any one-shot tooling it starts. `ciu`
means the project step may use the isolated workspace's CIU adapter and must
declare the CIU roots it needs. CMRU never infers this from Docker or Compose
commands and refuses a missing or unknown value. The workspace identity is
passed to steps as `CMRU_WORKSPACE_ID`, together with the workspace path and
source Git root, so runtime evidence can be tied to the exact candidate.
When a project step invokes `cmru`, CMRU supplies a temporary launcher bound to
the same runtime that started the transaction, puts it first in `PATH`, and
verifies its identity before the step starts. Project environment setup cannot
silently redirect nested CMRU commands to another installed wheel.
The rationale is in the [runtime-bound project command design](docs/DESIGN-GUIDE.md#project-commands-use-the-transactions-cmru-runtime),
and the complete config pair is in [CONSUMERS.md](docs/CONSUMERS.md#1-the-two-files).

## Verbs

```bash
cmru status                       # preview changed projects + next versions (read-only)
cmru release                      # isolated: prepare → gate → tag → build → publish → promote
cmru release --dry-run            # preview tags and external-version discovery
cmru release ciu                  # one project
cmru changelog assay --backfill-tag assay-v0.1.0  # catalog a pre-history release
cmru standards                    # strict config + project-framework conformance
cmru standards pwmcp --update     # safely update CMRU-owned revision markers
cmru build   <name>               # isolated local build; retains logs/artifacts, then removes worktree
cmru worktrees                    # list retained failed build/release worktrees
cmru abandon --dry-run            # inspect exact retained release candidates, no writes
cmru abandon <branch> --yes       # abandon exactly the named verified candidate
cmru abandon <branch> --config /path/to/cmru.orchestration.toml
cmru dependencies                 # show + preflight the project dependency graph
cmru dependencies --write         # refresh its generated root-TOML comment block
cmru run ciu --dry-run            # preview selected/default steps and commands
cmru tool-deps                    # verify declared tool dependencies: integrity/authenticity/freshness
cmru tool-deps --allow-stale-tool-deps   # proceed despite a stale (behind-latest) pin
cmru tool-deps --refresh <provider-project>  # deliberate external/copy artifact re-vendor + pin/hash update
cmru versions init [all|P[,P...]] [--dry-run]    # derive registry targets from manifests
cmru versions resolve [all|P[,P...]] [--dry-run] # resolve eligible versions and write native artifacts
cmru versions check [all|P[,P...]] [--json]      # read-only comparison with fresh registry state
cmru publish <name>               # caller-worktree push step
cmru publish <name> --build-output ID  # publish exact retained build bytes
cmru resolve <name>               # resolve the current "latest" (version/tag/url/sha256)
cmru cleanup --remove-assets 30d --dry-run  # preview age-based remote cleanup
cmru cleanup --remove-assets 30d --yes      # apply the reviewed cleanup actions
cmru cleanup ciu --delete-unmanaged-release-tag ciu-wheel-latest --dry-run
cmru cleanup ciu --delete-unmanaged-release-tag ciu-wheel-latest --yes
cmru cleanup ciu --delete-build-output <commit-date>_<commit> --dry-run
cmru cleanup --discard-build-worktree /path/reported/by/cmru --yes
cmru version                      # print the CMRU version
cmru --version                    # estate-wide top-level compatibility spelling
cmru get-py ciu --config cmru.orchestration.toml --output ciu-get.py  # render from installed wheel
cmru --help                       # generated verb catalog; use `cmru help <verb>` for options
```

These are representative operator workflows. The complete registered grammar,
including `cmru-agent`, `cmru-controller`, nested handler verbs, the supported
handlers module adapter, every option, and the required semantic review table, is maintained
in the [canonical CLI spec](docs/SPEC.md#s-cli9-canonical-cli-grammar-and-semantic-audit).

`cleanup` applies the configured remote asset policy: GitHub Release records and their
assets, release tags covered by that policy, and GHCR package versions. Every mutating cleanup
mode first displays pending actions and asks for confirmation; `--yes` accepts that displayed
set, and `--dry-run` stops after the preview. CMRU applies the captured target IDs and names
after confirmation instead of rediscovering targets, so an asset that appears or ages into the
policy while the prompt is open is not added without a new preview. For each Git tag, it also
captures the exact local and remote object IDs and deletes only refs that still point to those
objects when confirmation is applied. A tag created after the preview or retargeted meanwhile
is left for a later preview. CMRU refuses a successful remote tag listing that contains malformed
records, invalid Git refs, or tags outside the requested prefix, so an incomplete listing cannot
silently omit a tag from the cleanup plan. Inspect origin access and rerun the preview after
correcting the Git response. Before deleting a GitHub Release
record, CMRU re-fetches its
captured Release ID and skips it if the tag, update time, asset inventory, or displayed eligibility
changed. Before deleting a GHCR version, CMRU re-fetches its exact ID and skips it if its update
time or container tag set changed, or it no longer qualifies. If CMRU skips a Release, it also
keeps that Release's Git tag. Whole-package deletion is planned only for a package CMRU can read
during the preview; CMRU re-fetches its ID before deletion and skips if that ID changed. A GitHub
404 for a package or version means it may be absent or inaccessible, so CMRU reports that it
cannot verify cleanup and does not certify absence. The `ghcr_packages = ["*"]` selector lists
packages visible to the configured GitHub credential; it cannot include packages that credential
cannot read. The GitHub whole-package delete endpoint is name-addressed, so this identity recheck
is immediately before the request rather than an atomic compare-and-delete. A declared `steps.clean` and its
generated-file commit are included in the same plan. The preview's `CMRU_VERSION` is an estimate;
after confirmation, the clean step receives the highest-semver Release that actually survived
the applied actions. If the step changes files, CMRU
commits them even when no release tag was deleted, staging only paths that became dirty during
the step; paths already dirty when the confirmed plan starts remain outside it. Cleanup does not
remove a retained local release transaction or its `cmru-release-*` candidate
branch. `abandon` is
that separate lifecycle operation: it shows the exact scope, worktree, and remote candidate
ref before confirmation, rejects evidence of publication or promotion, and keeps its refusal
closed when transaction metadata or origin state is unclear, including when a successful
origin lookup omits `origin/main` or reports a malformed ref. `--dry-run` performs no
branch, worktree, sidecar, or remote mutation. New release transactions record the exact
origin tag set before release work starts. Abandonment refuses selected-scope origin tag refs
added, removed, or retargeted during the attempt, including refs whose tags point at the original
snapshot commit. The check compares
the full tag-ref inventory for release prefixes in the recorded project scope, so an added,
removed, or retargeted scoped ref blocks abandonment even if it is not reachable from the candidate;
other projects' prefixes do not. After
confirmation, CMRU rechecks the inspected branch and tag refs and deletes the candidate with
an exact `--force-with-lease`. The tag comparison covers only prefixes for projects in the
recorded scope. CMRU removes a local release tag during abandonment only when its sidecar proves
that the candidate attempted to push that exact object and the origin snapshot proves it is new.
An unexplained local-only tag in a selected project's namespace blocks abandonment; inspect it
and, if it is an unneeded local ref absent from origin, remove it with `git tag -d TAG` before
retrying. An older retained candidate without a tag snapshot is withheld when a scoped remote tag
reachable from it cannot be classified as pre-existing. See the [cleanup and abandonment design](docs/DESIGN-GUIDE.md#remote-cleanup-and-local-transaction-abandonment).
If the release used an external orchestration file to define a multi-project scope,
pass that same file with `cmru abandon --config PATH` so CMRU can inspect the complete
release policy before deciding whether abandonment is safe.

`cmru worktrees` includes retained paths recorded by Git even when they are not
reachable through the current bind mount. Its `prunable` field reports Git's
registration marker, not filesystem visibility; the listing preserves the
reported commit and withholds actions for marked entries. Every offered action
still validates the exact checkout before changing Git state. See the
[Git-family design note](docs/DESIGN-GUIDE.md#git-family-is-separate-from-cmru-root).

For a retained release, `cmru worktrees` also shows the recorded project scope
and a scope-safe resume command. `cmru release --resume PATH` derives that saved
scope when one is available; an explicit target must match it. Pass the same
`--config PATH` again if the failed release used an external config; the path is
not recorded in the transaction. Older candidates without scope metadata require
an explicit target after inspection. For scripts, release rows in
`cmru worktrees --json` expose `project_scope` and `project_scope_state`:
`project_scope_state` is exactly `recorded`, `missing`, or `unreadable`, and
`project_scope` is the recorded project-name array only in the `recorded` state
(otherwise `null`). Build rows omit both fields. See the
[release recovery design](docs/DESIGN-GUIDE.md#release-resume-keeps-its-recorded-scope)
and [consumer steps](docs/CONSUMERS.md#resuming-a-retained-release) for the JSON
handling rules.

A legacy release worktree without a shared CMRU ownership record is adopted only when its
registered Git worktree, release branch, shared Git family, and valid release-progress commit
in the candidate history all agree. CMRU writes the ownership record before
starting the resumed child; a branch name or child environment alone never
authorizes an in-place transaction.

## Supply-chain age-windowed versions

`cmru versions` selects registry releases older than the configured age window (14 days by
default) for `.pypi`, `.npm`, `.go`, and `.oci` targets. OCI targets use `selection = "semver"`
by default; set `.oci.selection = "rolling"` to age-check a literal rolling tag such as
`26-slim` or `bookworm-slim`. Rolling-tag records include the registry manifest digest, and
`check` detects a digest change even when the tag is unchanged. `init` derives package targets from
project manifests using the configurable discovery scope: shipped dependencies by default,
including explicitly selected Python extras, or all supported manifest groups when opted in.
`resolve` is the explicit write step for resolved state and native lock or
constraint outputs; `check` queries registries and reports the current recorded and eligible
versions without writing. A release timestamp exactly at the age cutoff is eligible; only later
timestamps are refused. Go `.info` times are VCS commit times, and OCI image-created timestamps
are publisher supplied; CMRU calls both out in warnings and the report. For multi-platform OCI
indexes, known Docker attestation manifests are excluded from runtime age calculation. Go targets
also check a constrained pseudo-version and use
the proxy's `@latest` fallback when no listed version matches. A resolve inside a Go workspace can
update `go.work` and `go.work.sum` along with module files; inconsistent Go timestamps fail closed.
These commands do not run as part of
build, release, gate, or a schedule. Read [the design guide](docs/DESIGN-GUIDE.md#supply-chain-age-windowed-version-determination)
for the scope policy and timestamp choices, and use the [consumer examples](docs/CONSUMERS.md#using-a-supply-chain-age-window)
to configure targets and consume the generated artifacts. Registry HTTPS redirects are followed
without forwarding credentials to a different origin; see the guides for the credential behavior.

Both version spellings print exactly one `cmru <version>` identity line to
stdout and exit 0 without diagnostics on stderr. At every parser depth,
`--help`, usage, missing-argument, unknown-argument, and other configuration
diagnostics begin with the CMRU headline as line 1. Normal command output is
unchanged. The compatibility rationale is in the
[design guide](docs/DESIGN-GUIDE.md#top-level-version-compatibility).

`release` detects changed projects, runs their explicit prepare/gate/tag/build/push/promote
contract in dependency order, and retains a failed transaction for diagnosis. It publishes
from the exact gated candidate commit, then fast-forwards `origin/main` from that same commit.
The exact release tag must be on `origin` before CMRU starts the build or publisher. After a
failed push, CMRU checks origin: if the tag is absent, it removes that exact local tag and retains
an untagged candidate that can be resumed; if origin confirms the exact candidate tag, it
continues; when origin cannot be checked, it retains the tag and candidate for inspection.
A concurrent remote update fails closed and leaves the candidate branch/worktree for diagnosis;
CMRU never rebases a candidate after building its public artifact. See
[KI-06](KNOWN_ISSUES_TODO_BACKLOG.md#ki-06--durable-post-tag-publication-resume--open-scoped-deliberately).
When resuming, CMRU installs copied credential overlays through no-follow paths and refuses
symlink or nonregular destinations in the retained worktree.
Release planning refuses when Git cannot read a project's history; it does not
treat a failed history query as an unchanged project. See the
[versioning design](docs/DESIGN-GUIDE.md#release-history-errors-refuse-the-plan).
`build` creates an isolated, commit-addressed local output record. To publish those exact bytes,
use the ID printed by `cmru build` with `cmru publish <project> --build-output ID`. CMRU
revalidates the manifest and every artifact digest before invoking that project's declared
`push` step. Built-in wheel and tarball handlers consume the retained files and rehash every
staged upload against `build.json` before contacting GitHub; a changed copy is refused. A custom
publisher must read `CMRU_BUILD_OUTPUT_ROOT`, compare any staged copy with `build.json` before
uploading it, and must not create or move Git refs when this option is used.
Publication is refused if prepare or build left any tracked or untracked source-tree changes in
the retained record; ignore expected untracked generated output paths such as `dist/` in the
project's `.gitignore`, without hiding source paths. Ignore rules do not hide modified tracked
files.
Built-in publishers require the existing versioned release/tag to identify the recorded source
commit and require the existing `-latest` release/tag; they update release assets in place and
create no Git refs or release records. They capture each existing Release ID and tag commit, then
recheck both before updating metadata and before each asset deletion or upload. GitHub offers no
atomic compare-and-update endpoint, so a remote change after a successful recheck can still race
the next API call. Generated sidecars and `latest.json` use temporary copies, so the retained
inventory remains reusable. A retained stable build older than the highest current
version can refresh its versioned assets, but CMRU leaves `-latest` on the newer release. CMRU
checks again before writing the pointer in case a newer release appeared during the upload. This
does not promote a source branch. `release`
remains the source-first tag/build/publish/promote workflow. See the [KI-10 decision](KNOWN_ISSUES_TODO_BACKLOG.md#ki-10--publish-retained-build-output-by-id--shipped).
After the transaction, CMRU reports whether caller `main` was synchronized. A dirty caller
checkout—including ignored files or directories—is left untouched before any rebase attempt,
including when `--allow-uncommitted` was used; see the [caller-main cleanup guidance](docs/RELEASE-TRANSACTIONS.md#caller-main-cleanup)
and normative [S-CLI.5a](docs/SPEC.md#s-cli5a--projects-release-one-after-another-not-in-a-shared-batch).

`cleanup --delete-unmanaged-release-tag TAG` is deliberately narrow migration maintenance:
it selects one project, accepts only that project's namespace, displays the exact GitHub
Release, then asks for confirmation unless `--yes` was supplied. It leaves the Git tag
untouched. It cannot
be mistaken for policy cleanup of normal immutable `<project>-v<semver>` releases.

## Logging and live diagnostics

Use the native release command directly—no wrapper or `2>&1 | tee ...` is required:

```bash
cmru release assay
```

It overwrites the root `cmru.release.log` with the complete release transcript.
The terminal stays readable: CMRU reports command labels, duration, known test-framework
success evidence, and concise failure excerpts. Detailed subprocess output is line-flushed to
the audit log and the transaction-local project files such as
`assay/logs/cmru/run-tests.log`. A successful release retains those project logs, declared
artifact directories, and explicitly declared gate evidence by default before removing the
worktree: logs move to `assay/logs/cmru-release/<immutable-tag>/`, declared directories move
into `assay/artifacts/<immutable-tag>/` with the existing hash inventory in `release.json`, and
gate evidence moves into `assay/evidence/cmru-release/<immutable-tag>/` with an `evidence.json`
source-commit/hash manifest. Pass `--discard-logs-on-release`,
`--discard-artifacts-on-release`, or `--discard-evidence-on-release` to opt out of each half.

```bash
cmru release modern-debian-tools-python-debug --show-run-details
cmru release assay --log-append
cmru release assay --log-prefix-time-short
```

`--show-run-details` also streams raw Docker/test output to the terminal. `--log-append`
preserves the prior root and per-step logs, adding an exact `---` divider before the new run.
CMRU sets `PYTHONUNBUFFERED=1` for Python child processes and flushes every received line;
non-Python tools must still flush their own output. `--log-prefix-time-short` adds
`HH:MM:SS` before CMRU's existing severity prefix. INFO/WARN/ERROR are colour-coded only on
an interactive terminal; `cmru.release.log` and pipes remain plain ANSI-free text.

## Project framework and templates

Each project owns one complete `cmru.toml` contract: identity, versioning, release artifacts,
environment, and every runner step. That file is portable to a fresh repository root. A
monorepo's nearest `cmru.orchestration.toml` contains central GitHub/target facts,
selection, order/dependencies, cleanup, and no project commands. It establishes the CMRU root.
`template_revision = 4` lets
`cmru standards` identify stale adoption without inventing project behavior. Ready-to-copy
examples are [`templates/cmru.toml.tmpl`](templates/cmru.toml.tmpl) and
[`templates/cmru.orchestration.toml.tmpl`](templates/cmru.orchestration.toml.tmpl).

`cmru standards --update` changes only those CMRU-owned markers. It never rewrites a project’s
build/publish commands; a remaining warning is a real policy decision to review.
Runner controls live in the project’s `[steps.<name>]` table, require explicit `quiet`, and
require `quiet = true` for the normal summary-only transcript; use `--show-run-details` when
live subprocess output is required. They reject unknown keys. Put project-only data beneath `[project_metadata]` so a misspelled
execution setting fails before it can alter a release. `cmru.build.toml`, shell sourcing, and
configuration aliases are retired; there is no compatibility parser.

The stock `tester-gate` command additionally requires an explicit tester image, memory,
combined memory/swap, CPU ceiling, host-systemd probe image, and the host gates slice
(`CMRU_TESTER_CGROUP_PARENT`, normally `${CGROUP_PARENT_DEV_GATES}`) in `[env]`. A gate that
uses `--enable-docker` must also declare its nested-Docker image. The stock `wheel-build`
handler requires an explicit wheel-builder image. These are release inputs, not CMRU
defaults; pin immutable digests in a production contract.
The CPU ceiling must be a finite decimal Docker can enforce (at least `0.00001` CPUs). CMRU
uses Docker's `--cpus` limit and refuses values below that bound before host probes. Docker
rejects `--cpus` and `--cpu-period` together, so CMRU does not set a separate CPU period.
See the [tester-gate CPU ceiling rationale](docs/DESIGN-GUIDE.md#tester-gate-workload-cpu-ceiling).
These CPU and memory inputs currently bound the tester workload; the optional DinD sidecar
shares the gates slice but has no separate per-container resource cap yet, as recorded in
[the canonical CLI audit](docs/SPEC.md#s-cli9-canonical-cli-grammar-and-semantic-audit).

`cmru tester-gate --dry-run` prints the exact workload Docker command and, when
`--enable-docker` is selected, the DinD startup command. It starts no container and skips
host slice and IO-support checks because those checks use temporary privileged containers.
The output names this limitation. Actual runs perform the checks before starting the gate;
the checks, DinD sidecar, and workload all use the configured gates cgroup parent. See
[the tester-gate contract](docs/SPEC.md#s26a--tester-gate-environment-preflight-ki-17).

## Release history is automatic

Every CMRU-managed project gets a project-local `CHANGES.md` by default. No project
script, config opt-in, or pre-created file is required. During `cmru release`, CMRU
derives the project-scoped git range, writes one marked entry, commits it with any
declared mechanical inputs, and runs the release gate against that commit. A tagged
release is headed by its pending version; an image-only release is headed
by the source revision it describes and advances a persisted source cursor. Generated
history and other declared mechanical outputs are excluded from the next source range.
If an image's private `prepare` step changed declared provenance but has no new source
commit, CMRU records a metadata-only history entry for that real new image; a clean
retained resume adds nothing.

Use `[project.release] changelog = "docs/CHANGES.md"` only to choose another
project-relative filename. `changelog = false` is the deliberate, reviewable opt-out.
Never add the usual `CHANGES.md` opt-in just to enable the feature—it is already on.

For a release that was published before this default existed, use the migration helper:

```bash
cmru changelog assay --backfill-tag assay-v0.1.0
git diff -- assay/CHANGES.md
git commit --only -m "docs(assay): backfill v0.1.0 release history" -- assay/CHANGES.md
```

It does not move the immutable tag. The resulting entry is visibly marked
`backfilled-after-release`; all future entries are source-first and carried by their
release tag.

## Reproducibility & the commit model

## Isolated release transactions

Run `cmru release` from your ordinary checkout—even if unrelated work is in progress.
cmru fetches `origin/main`, rejects local-only `main` commits that the snapshot would omit,
and creates a temporary `cmru-release-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>` worktree at that exact
commit — chronologically sortable, and flat, so the `.worktrees/` directory name is
byte-for-byte the branch name (true 1:1, matching ciu's `<prefix>-<YYYYMMDD_HHMMSS>-<feature>`
naming). The shared allocator derives a six-character lower-case base-36 identity from an explicit
canonical allocation path recorded in the shared workspace record (the final visible basename
contains that identity, so hashing the basename itself would be circular) and refuses collisions
while naming both paths. Worktrees retained under the
older nested `cmru/release/…` naming are still recognised. A local `main` behind the remote is warned about but safe because the
remote is authoritative. This matters because setuptools-scm sees the
whole Git worktree: a harmless edit in another project can otherwise make a wheel dirty.
The release child reads registered project configs from this isolated snapshot; when the central
orchestration file is external, CMRU maps its project paths from the source Git root into the
snapshot.

cmru ignores every uncommitted caller path, including a selected project's files: none can
enter the remote snapshot. It instead rejects only committed local `main` changes not yet on
`origin/main`, since those are easy to mistake for released source. Before touching any
project, cmru also checks the release plan itself against `origin` in two ways a purely local
read cannot: a project's latest tag must actually be published there under the SAME name AND
pointing at the SAME commit (never a local-only or same-named-but-different-object hand-made
tag), and `origin` must not carry a newer matching tag this local clone never fetched — either
always aborts with a named remedy. If a verified tag's commit is exactly the snapshot commit,
that's the ordinary state right after a completed release: cmru reports it and moves on, never
an error. Only a tag strictly *ahead* of the snapshot — pushed, but not yet in this snapshot's
history, almost always a half-completed prior release — aborts with a named remedy
(`--allow-tag-ahead-of-head` downgrades only that one deliberately). Any such plan-time refusal is a clean, typed failure that discards the
just-created worktree — never retains it, since no project's cycle ever started. In the
transaction worktree, cmru runs each changed project's required `run-tests` gate, then
fast-forwards `origin/main` from the validated branch before creating tags or publishing.
If another writer advanced remote main, the final candidate promotion fails closed after the
artifact step. A failure keeps the branch/worktree for diagnosis; success removes both (after
optional evidence retention).

When selected products live in independent Git repositories, CMRU runs one isolated transaction
per Git family. Each repository gets its own lock, branch, workspace identity, and promotion
result; the coordinated release is ordered but cannot be one atomic cross-repository commit.

`cmru build` uses the same remote snapshot and transaction mechanics but stops before source
release actions. On success it copies project logs to
`<project>/logs/<commit-date>_<full-commit>/` and declared artifact directories to
`<project>/artifacts/<commit-date>_<full-commit>/`, writes `build.json` with a SHA-256
inventory, then removes the worktree. These gitignored records can be consumed locally or sent
through `cmru publish --build-output ID` after CMRU rechecks their manifest and bytes. That
publication requires existing GitHub Release/tag targets; a stable version tag must resolve to
the build's recorded source commit. CMRU updates release assets without creating or moving Git
refs or promoting a source branch. It does not change the retained record while generating
publication metadata.
If the build or retention fails, CMRU keeps the exact
`cmru-build-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>` worktree and prints its
path. Run `cmru worktrees` to discover retained build/release worktrees, then use
`cmru cleanup --discard-build-worktree <path> --yes` only after inspection. An existing output
coordinate is never overwritten; remove it explicitly with
`cmru cleanup <name> --delete-build-output <id> --yes` before rebuilding that source.

`steps.prepare` is for deterministic source preparation, such as resolving an upstream
version. It may change only paths declared in `release.commit_generated`; cmru commits those
mechanical outputs before the gate. Use `version.strategy = "external:VAR"` when prepare
writes a derived version into `<project>/cmru.vars`: cmru reads it and owns the annotated tag.
During `release --dry-run`, CMRU runs only this declared external-version preparation inside
the disposable release candidate first, so the preview includes the version the real run would
use. It performs no gate, tag, build, push, or promotion, and never writes the caller checkout.
Never use a build or publish step to make an unreviewed source commit.
See [the release-transaction guide](docs/RELEASE-TRANSACTIONS.md) for recovery,
project-author requirements, and the current gate-adoption audit.

## Tool dependencies

A project's OWN tests/tooling may consume a first-party artifact released by ANOTHER
project in the same estate. Internal vbpub consumers such as cmru use the selected
worktree's `assay/` source directly from their run-gate lane, so they need no
`[[project.tool_dependencies]]` entry and cannot silently drift behind the source
being reviewed. A genuinely external or copied consumer may still vendor an
immutable artifact and declare it below.

`[[project.tool_dependencies]]` in `cmru.toml` makes that edge explicit; the
[consumer config example](docs/CONSUMERS.md#1-the-two-files) shows a complete loadable
project and orchestration pair.

`cmru dependencies` reports it as a third edge kind (`tool`, alongside `declared` and
`artifact`) but — deliberately, and permanently — never validates it against
`project_order`: routing it through that same check would make cmru→assay a cycle
against assay→cmru and refuse to load a config that is not actually broken.

`cmru tool-deps` runs three DISTINCT checks per declared dependency, never conflated in
either code or their messages:

* **Integrity** — do the vendored bytes match the recorded `sha256`? Local only, no
  network, always resolvable.
* **Authenticity** — does that hash equal the digest of the PUBLISHED release asset, for
  that project and exact pinned version? A file named `assay-1.0.0.pyz` is not thereby
  assay 1.0.0 — the published bytes are downloaded and hashed; the filename only picks
  which asset to fetch, never evidence of authenticity by itself.
* **Freshness** — is the pin the HIGHEST released version for that project? The staleness
  check, independent of authenticity: a pin can be authentic and simultaneously stale.

A stale or mismatched tool dependency is an **error by default** — both for `cmru
tool-deps` and inside `cmru release`'s own preflight (same phase as the tag-verification
preflight, before any project's cycle starts; scoped to only the projects this run
actually releases; runs identically for `--dry-run`). `--allow-stale-tool-deps` overrides
staleness only — there is no override for an integrity or authenticity failure. A fresh
clone with nothing released yet, or an unreachable network, is reported as a THIRD,
explicit `unresolved` outcome — never as a pass, and never as a failure. `cmru tool-deps
--refresh assay` re-vendors from the latest published release and rewrites the pin + hash
deliberately; nothing here ever refreshes automatically.

Two easy-to-miss traps this check is measured against: a repository with genuinely **zero**
releases returns HTTP 200 with an empty list (bootstrap, `unresolved`); a repository that
is missing/private/misspelled returns 404 on that *same* listing call, and a 404 there is
never read as bootstrap — it is a network/inaccessible outcome instead, so an estate that
loses access to a repo can't silently and permanently stop being checked. And because
resolving a dependency's provider (its tag prefix) needs the whole estate, `cmru tool-deps`
requires `cmru.orchestration.toml` whenever the selected project(s) declare one — a plain
`--config <project>/cmru.toml` invocation refuses cleanly with that explanation rather than
reporting a perfectly authentic pin as an authenticity failure it has no way to check.

**This verification never runs during `pytest`/`cmru tester-gate`.** That is not a
performance shortcut — it is the entire reason a pinned artifact is vendored instead of
fetched: the test suite stays hermetic, reproducible, and bootstrappable from a bare
clone with no network at all. See [SPEC.md S15](docs/SPEC.md#s15--tool-dependencies-declaration--verification)
for the full contract.

## Config & secrets

| file | committed? | purpose |
|---|---|---|
| `<project>/cmru.toml` | yes | complete portable project contract — **no secrets** |
| `cmru.orchestration.toml` | yes | nearest CMRU root: central facts, ordering/dependencies/cleanup |
| `cmru.secret.toml` | no (gitignored) | repository credential document (`schema_version = 1`, `[github].token`) |
| `<project>/cmru.secret.toml` | no (gitignored) | optional same-shaped project override, deep-merged over the root secret |
| `cmru.vars` | no (gitignored) | `KEY=VALUE` build vars a step emits for later steps |

**Token resolution (S2.4):** `$GITHUB_PUSH_PAT` → `$GITHUB_TOKEN` → deep merge the
selected CMRU-root `cmru.secret.toml` with the selected project's optional
`cmru.secret.toml` (the project `[github].token` wins). A committed `cmru.toml` token
is rejected. `GITHUB_PUSH_PAT`, `GITHUB_TOKEN`, and `CMRU_GIT_AUTH_TOKEN` are reserved and
cannot be set by project, orchestration, or step `[env]` tables. Supply publisher credentials
through the invoking environment or an ignored `cmru.secret.toml`. Never commit a token.

The resolved repository token also authenticates CMRU-owned GitHub HTTPS operations
(`cmru build`, `cmru release`, `cmru cleanup`, and `cmru abandon`) when `origin`
matches the configured `[github] owner` and `repo`. CMRU scopes it to the Git
process; it is not embedded in the remote URL, command arguments, or Git config.
SSH and other-host remotes keep their configured authentication, and a project
secret override remains specific to that project's publisher. CMRU disables local Git hooks
for credential-bearing Git calls because hooks inherit the Git process environment and could
reuse the repository token against another remote; the registered release gates remain the
release checks. Local hook-capable operations such as commit, revert, and rebase still run
configured hooks, with publisher token environment variables removed from Git and its hooks.
The local ancestry probes used by `cmru abandon` also remove those variables from the Git
child environment.
If no repository token resolves, Git's configured helpers and SSH authentication remain in
effect. See the [pasteable
secret file example](docs/CONSUMERS.md#3-the-release-flow-and-what-an-isolated-transaction-is)
and [Git transport design](docs/DESIGN-GUIDE.md#git-transport-authentication).

**Why `cmru.vars` is gitignored (and not a missing "starting point"):** it is a *generated scratchpad* — a build step writes computed values (e.g. pwmcp's playwright-driven version) for a *later* step in the **same** run to read. The committed starting point is git tags + `VERSION` files + `cmru.toml`; `cmru status`/`release` read those and never read `cmru.vars`. A fresh clone regenerates it on the next build. Committing it would turn a derived cache into an authoritative-looking input that drifts from the tags — the opposite of reproducible.

## Reusable project-step commands

CMRU exposes reusable components so a project can use its artifact and step
implementations without copying their logic. Install the approved CMRU wheel
into the interpreter that runs the project step, then choose the interface that
fits the work:

| Need | Interface | Role |
|---|---|---|
| Release, inspect, or maintain a product | `cmru` and its registered verbs | Canonical operator workflow |
| Register/build/publish an artifact handler from a project step | `python -m cmru.handlers …` | Explicit project-step adapter; also used by the fresh-checkout wheel bootstrap |
| Preview or reproduce one declared step | `cmru run-step …` | Direct single-step diagnostic using the project's normal `cmru.toml` |
| Compose step or bundle behavior in Python | `cmru.runner.run_step` or `cmru.bundle.run_bundle` | Supported library entrypoints used by estate consumers |
| Manage generic Git worktree lifecycles | `worktree` package in the CMRU wheel | Stable shared API, versioned with the CMRU wheel; see the [worktree consumer guide](../libraries/worktree/CONSUMERS.md) |

`cmru.bundle` builds an archive from its dedicated bundle TOML configuration;
the deterministic format is `xztar` as specified in S9. It is a Python library,
not a module CLI or top-level `cmru bundle` verb. A project that needs the
reusable operation imports `run_bundle`; a root verb should be added only when a
concrete operator workflow needs one. `cmru run-step` is the single-step CLI.
The standalone generated `get.py` remains intentionally independent and uses
`argparse` because adopters run it without a CMRU installation. The
[consumer guide](docs/CONSUMERS.md#using-the-wheel-and-component-interfaces)
shows installation and invocation examples.

The OCI helper has an explicit normal Buildx bake load/push command. Its `--repack` argument
is intentionally fail-closed while production-equivalence evidence is absent; use a
project-owned, tested flow such as MDT's for real OCI repacking.

## Differentiators

1. **N products, one Releases page** via per-product `prefix` (`ciu-v…`, `pwmcp-v…`).
2. **Per-product "latest"** — `cmru resolve` returns the highest-semver release for a prefix; `<prefix>-latest` holds a thin `latest.json` pointer, not a duplicated asset.
3. **Explicit publication contracts** — wheels, OCI images, bundles and tarballs use one
   strict runner grammar, while each project owns its artifact-specific commands.
4. **Per-interpreter variants** (S-REL.6) — a `bundle`/`tarball` may declare `[[project.variants]]` so one tag publishes one asset per variant (`<tag>-<variant><suffix>`); the generated `get.py` installer selects one explicitly with `--variant NAME`. Zero declared variants keeps the single-asset path unchanged.

## cmru vs ciu

cmru is the **outer loop** (build-to-release: version + publish across products). Its sibling **ciu** is the **inner loop** (build-to-run: build local images and run a stack on this host). They overlap only in that both can trigger a docker build — over the *same* `docker-bake.hcl`, for different ends (ciu `--load`s + runs; cmru pushes). Full map, incl. the border question: [`../docs/ciu-vs-cmru.md`](../docs/ciu-vs-cmru.md).

## Adopting cmru for your own product

Making a product releasable by cmru — a portable `cmru.toml`, the orchestration `[env]` a
`tester-gate` step needs, wiring a project into the estate, and the failure modes worth knowing
before your first release — is covered step by step in **[`docs/CONSUMERS.md`](docs/CONSUMERS.md)**.

## More

- Adopting cmru (the HOW): [`docs/CONSUMERS.md`](docs/CONSUMERS.md).
- Design rationale (the WHY): [`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md).
- Full contract & rationale: [`docs/SPEC.md`](docs/SPEC.md) — start at *S-CLI* and *S-REL*.
- Monorepo tooling overview: [`../docs/RELEASE-TOOLING.md`](../docs/RELEASE-TOOLING.md).
- Release-modes design/plan: [`../docs/plan-cmru-release-modes.md`](../docs/plan-cmru-release-modes.md).

## Testing

`./run-gate.py` is the canonical test entrypoint — `./run-gate.py --list`
discovers the declared lanes; definitions live in `run-gate.toml`. The
`assay` lane declares R0/R1/R3: the full suite, 100% line+branch coverage,
and an import-break canary. The release `gate` adds R2 through a separate
changed-source mutation campaign based on the highest-version published
`cmru-v*` tag in the candidate's full ancestry.
The registered `gate` lane queries every CMRU release tag on origin through
CMRU's credential-scoped Git transport, then forwards only token-free tag and
commit facts to the `cmru-mutation` tester environment as
`CMRU_ASSAY_BASELINE_FACTS`. The mutation lane checks those facts against its
HEAD and requires the selected tag to match the configured Assay R1 base. On
an untagged candidate, that ancestor must also be the latest published CMRU
release. On a tagged-HEAD rerun, every release tag at HEAD is excluded and the
selected ancestor is the highest-version published release in the remaining
ancestry; the latest published tag may be one of the verified tags at HEAD. It
also checks Assay's effective
comparison commit: if Assay resolves a merge's first parent after the tag, the
configured CMRU source roots must be unchanged across the gap. A missing tag or
mismatch fails the gate. Run the registered `gate` lane so it prepares fresh
origin facts immediately before mutation.
The checker verifies every local CMRU release tag at HEAD against its exact
origin commit, including older tag names that point to the same commit.
The registered real-enrollment lane sets `CMRU_ENROLL_REQUIRED=1`; missing Docker or host-probe
configuration, an unloaded or fragment-less gate slice on the Docker host, or a failed fixture-image
build fail that lane instead of skipping O2/O3. It checks the host through CMRU's privileged systemd
probe. Direct local test runs may still skip the container integration checks when prerequisites
are unavailable.
Before tester-unified starts, the host gate points all visible root and selected
project secret overlays at private host backups outside the repository mount
and strips publisher-token and extra-mount variables from nested runner calls.
It uses CMRU's scoped Git auth on the host to prepare token-free origin facts
for mutation, then restores the original secret files. If restoration fails,
the gate reports and retains the private backup path for recovery. If a credential is atomically
replaced while the gate masks or runs, CMRU preserves the replacement and fails the gate instead
of replacing it with stale bytes. SIGTERM and SIGHUP also run the restoration path. See the
[gate credential design](docs/DESIGN-GUIDE.md#keeping-release-credentials-out-of-gate-containers).
When rerunning on a release-tagged HEAD, the checker excludes every local CMRU
release tag at HEAD and selects the highest-version published tag in its full
ancestry. An empty source diff records a skip bound to the candidate HEAD. The
selected worktree's Assay source is installed at lane time.
Keep each candidate's R1 base on the latest release published before that
candidate; advance the pin to a newly tagged release on the next release
candidate.
See the [Assay baseline design](docs/DESIGN-GUIDE.md#why-cmru-pins-assay-r1-to-a-release-tag-and-splits-r2-out).
Mutation runs stop each
failing candidate at its first failed test (`--maxfail=1`), cap each candidate
at 120 seconds, and resume killed outcomes from `.assay/mutation-cmru.json` only
while the test suite and copied fixture closure are unchanged. The append-only
`.assay/progress-mutation-cmru.jsonl` stream reports progress; it is not the
resumable result record. Any test or fixture change requires a new campaign. A
successful full-suite run still executes all tests. `.assay/` verdict, result,
and progress artifacts are retained as gate evidence.
See [`../run-gate-project/CONSUMERS.md`](../run-gate-project/CONSUMERS.md).
