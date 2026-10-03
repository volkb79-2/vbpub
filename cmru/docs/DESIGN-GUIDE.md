# CMRU design guide

This document records why CMRU's configuration and command selection work the way they do.
The [README](../README.md) describes the shipped surface; [CONSUMERS.md](CONSUMERS.md)
shows the files an adopter can copy and load.

The closed runtime contract is intentionally small and versioned: `runtime.kind` is either
`none` or `ciu`. The full project grammar is exercised by the shipped config reader and the
loadable pair in [CONSUMERS.md](CONSUMERS.md#1-the-two-files).

## Supply-chain age-windowed version determination

Version selection is an explicit data refresh. Builds and releases consume committed native
artifacts, so an upstream publication cannot silently change the dependency graph during a
release. `cmru versions check` performs a read-only registry query; `cmru versions resolve`
records the decision and writes the language-native outputs. There is no build, release, gate,
or scheduled refresh hook.

The 14-day default is a policy window, not a claim that age alone proves safety. A `single`
target chooses the newest eligible release from one source. An `aligned` target chooses the
newest version available and age-eligible in every declared source. The shared constraint
grammar is a SemVer-compatible subset so one target means the same version across PyPI, npm,
Go, and SemVer OCI tags. OCI `selection = "semver"` is the default; `selection = "rolling"`
accepts a literal non-SemVer tag. Rolling selection is limited to one OCI source with
`constraint = "*"`, cannot be aligned with package versions, and requires a registry manifest
digest. The recorded digest makes a moved tag visible to `check` even when its name remains the
same. A release timestamp exactly at the age cutoff is eligible for both SemVer and rolling OCI
selection; only timestamps later than the cutoff are rejected. An exact override requires a
reason; when its source timestamp is newer than the cutoff, it also requires a future expiry date.
The reviewable config and artifact diff remains the control for deliberate holds and urgent fixes.

Shared targets and their resolved records belong to `cmru.orchestration.toml`. A project may
redeclare a target under its own `[versions.targets]`; that project's age window and resolved
record then live in its `cmru.toml`, while the shared root result remains independent. A
project-level age-window value alone does not change a root target.

Age evidence is source-specific. PyPI JSON upload timestamps and npm registry version times
drive those sources. The Go proxy's `.info` time is the VCS commit time, not proxy publication
time; CMRU records that source name and prints a warning when using it. OCI registries may
provide an HTTP `Last-Modified` value; when they do not, CMRU accepts the publisher-provided
`org.opencontainers.image.created` annotation or image-config `created` field as the documented
fallback, with a warning. A source that provides no usable timestamp fails closed. These limits
are visible in the report so an age cutoff does not claim stronger evidence than it has.
Rolling tags use the registry's `Docker-Content-Digest` as immutable identity evidence; the
timestamp policy remains registry `Last-Modified` with the documented publisher-created-time
fallback. Multi-platform indexes may also contain build attestations; CMRU skips descriptors
marked `vnd.docker.reference.type = "attestation-manifest"` and checks age evidence on every
remaining runtime platform. Rolling selection queries the declared tag's manifest directly, so a
large registry does not need to enumerate its full tag collection.

Discovery defaults to the shipped dependency surface: Python project dependencies, explicitly
selected Python extras, runtime npm dependency tables, and declared Go requirements. Test and
build dependencies can change more frequently and do not necessarily ship with the product, so
they are excluded by default. A project may opt into `scope = "all"` to derive targets from all
supported optional/dependency groups and build requirements. This is a declared policy choice;
CMRU does not infer which extras a product ships from source imports or build scripts. Project-only
manifest facts such as selected Python extras and additional requirements files stay in that
project's `cmru.toml`.

Registry clients follow HTTPS redirects because registries can move blob bodies to signed storage
URLs. The [OCI Distribution Specification](https://github.com/opencontainers/distribution-spec/blob/main/spec.md)
permits redirects and says clients must not forward `Authorization` across hosts unless configured
to do so. CMRU retains it on redirects to the same HTTPS origin (scheme, host, and port), but
removes it when the origin changes. HTTPS-to-HTTP redirects and redirect URLs with embedded
credentials or fragments are refused. Registry and Go proxy endpoint URLs also reject embedded
credentials, queries, and fragments so secrets and endpoint selection stay in the declared
environment-backed auth fields. A redirected host that requires its own credentials must be
configured as the source's canonical registry endpoint.

The Go proxy's `@v/list` omits pseudo-versions. CMRU checks a pseudo-version named by a Go
constraint through its `.info` endpoint and consults the proxy's `@latest` endpoint when no listed
version satisfies the constraint. In workspace mode, `go get` may also update `go.work` and
`go.work.sum`; resolve snapshots those files so a later writer failure rolls them back with
`go.mod` and `go.sum`. If `.info` and `@latest` report different commit times for the same version,
CMRU refuses the result because the age evidence is inconsistent.

The registry clients determine direct target versions and the Python writer asks uv to compile
their transitive dependency closure under the same cutoff. npm and Go use their native commands
to update lock/module state. OCI selection writes a small JSON record; projects that need another
manifest can opt into a Jinja2 output without adding a runtime dependency to ordinary CMRU use.
See the consumer guide for output paths, template context, credential variable names, and the
exact registry timestamp evidence.

## Top-level version compatibility

CMRU keeps its version verb and also accepts cmru --version at the top level.
Both spellings use the same source-tree or installed metadata resolver and
print cmru <version> on stdout with exit 0. The second spelling is compatibility
surface for scripts that probe every first-party estate CLI in the same way; it
does not create a second version source or change verb dispatch.

The same identity is the first line of every help, usage, and configuration
document emitted by the installed `cmru`, `cmru-agent`, and `cmru-controller`
dispatchers, including nested verbs. CMRU configuration diagnostics put the
identity first; `cli-extended` usage/refusal diagnostics put the actionable
message first and then render the matching generated help. Normal command output
is unchanged.

## One declared CLI grammar

The CMRU wheel installs three operator CLIs: `cmru`, `cmru-agent`, and
`cmru-controller`. Each uses `cli-extended` registrations as the source for
argument parsing, option constraints, help, and dispatch. Root `cmru --help`
stays a short command catalog, while `cmru help VERB` and `cmru VERB --help`
show that verb's complete grammar. Nested commands delegate their remaining
argv to their own registered CLI, so root help and child parsing cannot drift
through a second parser. The shared library provides `--help`; CMRU intentionally
does not keep a separate `-h` alias.

Invocation-wide options are registered in each CLI grammar and work before or
after command selection, including across delegated commands. The timestamp
prefix option is applied by its registered parser action, so CMRU does not
rewrite argv before dispatch.

`get-py` and `dependencies` are each one canonical verb; the old `get`,
`dependency-graph`, and `graph` aliases were removed because they added help,
testing, and compatibility surface without adding a use case. The same review
removed numeric `init --layout` spellings and the deprecated tag option alias.
The `handler` verb is the supported route to the project's explicit step
handlers; `cmru-agent` and `cmru-controller` are separate installed commands,
not hidden subcommands of `cmru`. The [canonical CLI grammar and semantic
audit](SPEC.md#s-cli9-canonical-cli-grammar-and-semantic-audit) inventories their complete option surfaces
and is updated with every product grammar change.

### Project commands use the transaction's CMRU runtime

A project step that invokes `cmru` must run the same CMRU code that started the
transaction. Resolving through ambient `PATH` could select an older installed
wheel and run a different command contract inside a current source checkout.
Before each step, CMRU creates a temporary launcher using its current Python
interpreter and module root, puts that launcher first in `PATH`, and checks its
reported version against the active runtime. The `CMRU_BIN` and `PATH` binding
is reapplied after project environment setup, so a project setting cannot
silently redirect a nested gate. A mismatch fails before the step command
starts. This keeps project contracts portable and avoids requiring each
consumer to know whether CMRU came from a wheel or source checkout (KI-11).

### Retained build publication names the artifact input explicitly

`cmru build` retains a local build with its source identity and complete digest
inventory. `cmru publish --build-output ID` validates that record and gives the
declared push step the retained files explicitly; ordinary `publish` continues
to use the caller checkout. CMRU does not infer the intended artifact from a
`dist/` directory or silently couple every build to publication. Built-in
publishers require existing versioned and `-latest` Release/tag targets; a
stable version tag must identify the retained source commit. They update
Release metadata and assets without creating or moving Git refs. The publisher
captures each existing Release ID and tag commit, then checks those exact
coordinates before metadata changes and before every asset deletion or upload.
GitHub does not offer an atomic conditional update, so a change after a check
can still race the following request; retaining the ID and rechecking the tag
target prevents a stale preflight from silently authorizing a later asset set.
Each staged artifact copy is hashed against the manifest before the first GitHub
request. Otherwise a source-file change between manifest validation and staging
could make a generated checksum certify substituted bytes. Temporary copies hold
generated sidecars and `latest.json`, leaving the retained record unchanged. When a stable retained build is older than the highest current
release, CMRU can refresh that version's assets but does not rewrite `-latest`.
It checks the highest version both before publishing and again before pointer
upload, which protects against a newer release appearing during the upload.
Custom push steps must consume the protected
`CMRU_BUILD_OUTPUT_ROOT` input, compare any staged copy with `build.json` before
uploading, and must not create or move Git refs or promote a source branch.
Publication refuses a retained record with any tracked or
untracked source-tree changes: otherwise the artifact could contain edits that
the recorded source commit and release tag do not identify. Consumers should
ignore expected, untracked generated build outputs, while keeping source paths
visible to Git status. Ignore rules do not hide changes to tracked files.
`cmru release` owns the separate source-first flow.
See [KI-10](../KNOWN_ISSUES_TODO_BACKLOG.md#ki-10--publish-retained-build-output-by-id--shipped).

`cmru run` keeps orchestration's configured `default_steps` when no explicit
step flag is supplied; help names that policy because a default may include
publishing. Its dry-run resolves the same target, steps, and configured order,
prints the declared commands and file cleanup, and starts no project command.
Remote cleanup lives only under `cmru cleanup`: it prints the pending action
set and asks before changing anything unless `--yes` was supplied. A cleanup
dry-run stops after that preview. The confirmed action set is captured before
the prompt and applied as-is; rescanning would widen the deletion to targets
the operator never saw, including an age-based asset that crossed its cutoff
while confirmation was open. Git-tag targets carry the exact local and remote
object IDs from that preview. Conditional local ref deletion and a remote
lease ensure a newly created or retargeted ref is not removed by a confirmation
that named the previous object. The canonical semantic table records each
verb's scope, combinations, defaults, writes, network effects, and dry-run
boundary.

`tester-gate --dry-run` does not run the host checks. Both the systemd slice check and
the optional IO-controller check need a temporary privileged container; starting one would
break the promise that dry-run starts nothing. The preview therefore shows the workload
command and any DinD startup command, prints that the host checks were skipped, and keeps
the `--cgroup-parent` argument visible. Actual execution performs the checks before it
starts the gate, and places every helper and workload container in the declared gates tier.
This makes the boundary honest: dry-run proves command construction, while a real launch
proves host acceptance.

### Tester-gate workload CPU ceiling

The tester workload container must have a per-container CPU ceiling in addition to the
aggregate gates slice. An empty value is already refused; a live Docker check showed that
`--cpus 0` is accepted but records `NanoCpus=0`, `CpuQuota=0`, and `CpuPeriod=0`, which
leaves the container without a per-container CPU limit. Live runs also showed values below
`0.00001` CPUs produce `cpu.max = max 100000`, while `0.00001` produces a bounded quota.
CMRU accepts only a finite CPU value of at least `0.00001` that Docker can represent, whether
supplied through `--cpus` or `CMRU_TESTER_CPUS`. It uses Docker's `--cpus` field alone:
Docker rejects a HostConfig that supplies both `NanoCPUs` (from `--cpus`) and `CpuPeriod`
(from `--cpu-period`). The value is checked before starting privileged host probes.

The optional DinD sidecar is a separate container. It receives the same gates-slice parent,
but currently has no per-container CPU or memory cap. The CMRU S-CLI.9 audit records this as
an open policy decision because reusing workload limits can double the per-step resource
envelope, while separate sidecar limits add required inputs for the Docker-enabled path.

Docker also rejects a `--memory-swap` total below `--memory` before creating the gate
workload. Keep the value documented as a combined memory-plus-swap total; it is not the
swap-only amount. The canonical invocation and combination decisions live in
[`S-CLI.9`](SPEC.md#s-cli9-canonical-cli-grammar-and-semantic-audit).

The get.py template is a package resource, not a path inferred from `__file__`.
Source-checkout execution and installed-wheel execution therefore read the
same shipped file. KI-26's installed-wheel lane builds the distribution,
installs it into a fresh venv without system packages, and renders/compiles a
project installer from outside the checkout. That catches both omitted package
data and a missing bundled `cli-extended` import.

### Operator commands, adapters, and libraries have separate jobs

Installed console scripts and their registered verbs are the operator
interface: `cmru`, `cmru-agent`, and `cmru-controller`. They use
`cli-extended` to define grammar, options, help, and dispatch. CMRU does not
maintain parallel hand-written parsers for those commands.

`python -m cmru.handlers` is the one supported component CLI. Project step
contracts use it to invoke registered artifact handlers, and
`build-initial-standalone.sh` needs it to build the first wheel before the
installed `cmru` command exists. `python -m cmru.bundle`, `python -m
cmru.runner`, and module aliases for the three operator scripts are retired;
they now refuse with a pointer to the supported command or library API.

The reusable `cmru.bundle.run_bundle` and `cmru.runner.run_step` functions
remain supported Python APIs. PWMCP consumes the bundle API, and MDT consumes
the runner API. Use `cmru run-step` for a direct operator invocation. CMRU does
not add a root `cmru bundle` verb until a concrete operator workflow needs one.
The standalone generated `get.py` remains an independent product and keeps its
own `argparse` parser because adopters use it without installing CMRU.

`worktree` is a bundled shared library, not another CMRU CLI. It owns neutral
Git workspace identity, records, leases, and lifecycle primitives; CMRU owns
release and transaction policy. Its separate consumer guide is the canonical
place for those APIs and examples.

## Remote cleanup and local transaction abandonment

`cmru cleanup` follows the configured remote policy for GitHub Release records
and assets, matching tags, and GHCR package versions. A retained candidate is a
different object: its private branch, worktree, and transaction sidecars exist
to support inspection or resume. Removing one uses `cmru abandon`, with exact
branch selection, a dry-run plan, and an explicit confirmation. It refuses
published, promoted, untagged-publisher, or otherwise ambiguous transactions.
`--dry-run` only inspects state and renders candidates; no mutation helper is
called. The separate verbs keep local recovery from silently deleting public
assets and keep remote asset pruning from appearing to clean a retained source
transaction. Abandonment accepts `--config` because the release's external
orchestration document may be the only policy that names every project in a
multi-project transaction; guessing from the current directory can turn valid
scope members into apparent untagged publishers and falsely refuse recovery.
Remote promotion status is determinate only when the successful branch lookup returns
`origin/main` with a valid object ID. A successful but empty or malformed result does not mean
the candidate is unpromoted, so abandonment withholds cleanup until that ref can be inspected.
Tag ancestry alone cannot establish that a tag was published by the retained attempt: a new
release tag can point at the original snapshot commit, especially when a project has no changelog
commit. New transactions therefore store the complete origin tag-ref set before release work
begins. Abandonment compares only release-tag prefixes for projects in the recorded scope against
that immutable baseline; any added, removed, or retargeted ref in those namespaces blocks
abandonment, even if the tag is not reachable from the candidate. A missing legacy snapshot is
indeterminate when a scoped tag reachable from the candidate cannot be classified. An exact local
tag is removed only when the transaction sidecar records the
candidate's push attempt and the baseline proves that tag was new; unexplained local-only tags
block recovery for operator inspection. After confirmation CMRU rechecks branch and tag inventories,
then deletes the candidate branch with a lease against the inspected object ID before removing
local evidence.

Cleanup plans retain GitHub Release IDs and tag names, but an ID may later refer to a Release
whose tag, update time, asset set, or age-policy status changed. Applying the plan looks up that
ID again and deletes it only while those captured facts still match. Its Git tag stays in place if
the Release is skipped, and a `steps.clean` action derives `CMRU_VERSION` after the confirmed
actions complete; the version printed in a preview is an estimate and is re-resolved after those
actions. GHCR version actions also re-fetch the exact package-version ID and compare its update
time and container tags with the preview. Whole-package deletion is planned only for a package
the credential can read, then re-fetches its package ID immediately before the name-addressed
delete request. A changed package is skipped. A GitHub 404 is indeterminate because private
package APIs can hide inaccessible objects; CMRU reports the ambiguity and skips deletion instead
of calling the package absent. The wildcard package listing also contains only packages visible
to the credential. GitHub's Release and package APIs do not provide a compare-and-delete operation,
so a change after the final recheck remains outside the plan's control.

### Release resume keeps its recorded scope

A retained candidate stores the projects selected for that transaction. Resume
uses that recorded scope when the operator omits a target, and refuses an
explicit target that adds or drops a project. `cmru worktrees` prints the scope
and a scope-safe command when the metadata is readable. It reads the sidecar
from the shared Git directory using the inventory's branch fact; it does not
stat a listed worktree path that may belong to another filesystem namespace.
Release rows in `cmru worktrees --json` include `project_scope` and
`project_scope_state`. The state vocabulary is `recorded`, `missing`, and
`unreadable`; only `recorded` carries the exact project-name array. Consumers
must not resume from a missing or unreadable scope. If the failed release used
an external config, the operator repeats that same `--config PATH`; the path is
not stored in the transaction. A legacy candidate with no scope record needs an
explicit target after inspection. This keeps a failed single-project release
from expanding to every project that happens to be changed later, while still
letting a deliberate fresh release use the configured default scope.

A recordless legacy release checkout is not trusted from its branch name alone.
Resume first verifies its linked-worktree registration, exact source Git family,
release branch, and a progress commit that is an ancestor of the candidate, then
records it through the shared worktree lifecycle API. The child requires that
record's path, branch, source, workspace ID, and base to match its routing
environment. This preserves the older recovery path while making ownership
explicit before execution.

Cleanup freezes remote IDs and local build-record/worktree identities before
confirmation. For a declared `steps.clean`, CMRU snapshots dirty paths
immediately before running the step and commits only paths that become dirty
during it. This keeps edits made in the caller checkout before confirmation out
of the generated cleanup commit; a path already dirty before the step stays
outside it. A generated-file commit still happens when the clean step changes
files but the policy selected no release tags for deletion.

## Context comes from the nearest CMRU root

CMRU searches the current directory and every parent up to the filesystem root for the
nearest `cmru.orchestration.toml`. That file establishes the CMRU root, even when it sits
above several Git repositories. A nearer orchestration file starts a nested CMRU root. An
explicit `--config` path takes precedence over discovery.

The search is filesystem based because a user may deliberately keep shared orchestration
policy outside repository directories. A discovered project `cmru.toml` must be registered
by the selected central file. A nearer unregistered project file is an error instead of being
silently routed through an outer project, and symlinked paths cannot escape the CMRU root.

## Git family is separate from CMRU root

An orchestration root is policy scope, not a Git checkout. A registered project
may live in a nested repository, so invocation resolution carries the selected
CMRU root and the project's Git top-level/common directory as separate typed
facts. The shared workspace allocator uses the project Git family; it never
uses the orchestration directory merely because that is where
`cmru.orchestration.toml` was found. When one target spans independent Git
families, CMRU dispatches one isolated transaction per family. Each family
therefore gets its own lock, branch, workspace record, promotion result, and
recovery path; the operation is intentionally not one cross-repository atomic
commit.

This distinction also makes containment explicit: the CMRU root may equal the
project root, contain it, or sit above it. `..` and symlink escapes remain
configuration errors because a project registry must not reach an unrelated
checkout by path trickery.

Release children load the authoritative orchestration config from the isolated
source snapshot. Their project config paths may therefore already be rooted
inside that snapshot; CMRU uses those paths directly relative to the child root.
When orchestration policy is stored outside the checkout, project paths are
mapped from the source Git root into the child. Both paths must resolve inside
the selected source snapshot before a command can run.

CMRU names a transaction with the shared six-character workspace identity. The
final basename contains that token, so the adapter gives the neutral allocator
an explicit canonical allocation identity path and keeps it in the durable
record; resume and cleanup validate the same structured fact instead of
recomputing a second token from a circular final name.

Native worktree inventory also belongs to that neutral layer. Its NUL-framed
Git parser preserves literal paths and carries Git's `prunable` fact, avoiding
both duplicate product parsers and filesystem probes against a path recorded
in a different mount namespace. That marker describes Git's registration
state, not whether the directory is visible here. CMRU keeps prunable records
discoverable, preserves their HEAD, and withholds actions for marked entries;
each offered operation still validates the exact checkout through the shared
lifecycle preflight.

## Runtime ownership is declared

`[runtime].kind` is a closed project vocabulary: `none` or `ciu`. CMRU does not
inspect arbitrary Docker/Compose commands to infer lifecycle ownership. `none`
leaves one-shot runtime cleanup to the project command; `ciu` gives that command
the isolated workspace context so the CIU adapter can manage its declared roots.
The explicit declaration is validated before a runner starts, which keeps an
unknown or missing provider from falling through to a guessed Docker policy.

## Repository facts are central in an estate

`[github]` and `[targets]` describe the release destination shared by registered projects,
so an orchestration file owns them once. Project files retain release, build, gate, and
artifact policy and remain portable as standalone files by carrying those two tables when no
central file exists. This prevents duplicated facts from drifting while preserving a clear
standalone bootstrap path.

Repository secrets are resolved separately: the environment wins for an invocation, then the
CMRU root secret, then a project-local overlay. No credential is copied into a project config.
The resolved publisher token must remain the same credential through the runner and CMRU's
Git transport. For that reason, `GITHUB_PUSH_PAT`, `GITHUB_TOKEN`, and
`CMRU_GIT_AUTH_TOKEN` are reserved: config `[env]` and `[steps.*.env]` tables cannot replace
them after secret resolution. Operators supply them through the invoking environment or an
ignored `cmru.secret.toml`.

## Git transport authentication

The repository-root credential also covers CMRU's own GitHub HTTPS reads and writes. A release
uses Git for its source fetch, candidate branch, release tags, promotion, and cleanup; relying on
an unrelated interactive Git helper made `cmru.secret.toml` incomplete for the workflow that
already resolved that token. CMRU supplies it through a temporary askpass helper only when
`origin` is HTTPS on `github.com` and its path matches the configured `[github].owner` and
`repo`. The token stays out of the remote URL, process arguments, and persistent Git config; the
transport helper adds it only to that CMRU-owned Git process. Project publisher credentials
continue to follow the existing project runner contract. SSH remotes and other hosts retain their
own authentication; CMRU never forwards its GitHub token to them. For a credential-bearing Git
operation, CMRU points `core.hooksPath` at its private temporary directory. Git hooks inherit the
Git process environment and could otherwise reuse the token for a second remote; the registered
release gates are the checks for the release candidate. The askpass helper also refuses prompts
that do not identify `github.com`. When no repository token resolves, Git's configured helpers
and SSH authentication remain in effect. Hook-capable local operations (commit, revert, and
rebase) still run local hooks, but CMRU removes `GITHUB_PUSH_PAT`, `GITHUB_TOKEN`, and
`CMRU_GIT_AUTH_TOKEN` from the Git child environment first. The local ancestry probes in
`cmru abandon` use the same credential-stripping helper. This keeps local user hooks available
without exposing publisher credentials to them and ensures those local probes receive no
publisher token variables.

Repository operations use the CMRU-root credential because candidate refs and `main` belong to
the repository as a whole. A project-local secret override remains scoped to that project's
publisher. When CMRU releases itself, the installed entry point imports the release candidate's
source tree for the child transaction, so the CMRU implementation being shipped performs that
release's Git operations too.

## One target grammar for project-aware verbs

Every project-aware verb accepts an omitted target, `all`, one registered name, or a
comma-separated list. `all` is exclusive; names are trimmed, duplicates and empty entries are
errors; execution follows declared `project_order`. In a project directory, an omitted target
means that project. At a CMRU root, it means the configured estate selection. This makes
`cmru resolve`, `cmru build`, `cmru release`, and the other verbs behave the same way whether
CMRU is run from an installed wheel or a source checkout.

The public registry selector is positional. `--prefix` remains available only inside the
private artifact handler commands, where it names the tag prefix for a concrete publication
operation.

## The wizard asks before it writes policy

`cmru init` is an adoption wizard because release ownership cannot be safely inferred from a
directory name. It asks for the CMRU root, project folder and name, project type, GitHub owner,
repository, owner type, artifact inventory, release-tag policy, and commands where the selected
artifacts need generic handling. The artifact inventory accepts any comma-separated subset of
`wheel`, `tarball`, `bundle`, and `oci-image`, or `all`. Existing files, path escapes, malformed
choices, and missing generic commands are refused before anything is written. Every generated
file is then loaded by CMRU's real configuration reader.

## Native release logging replaces the wrapper

The old shell wrapper duplicated release dispatch and made the installed command and wrapper
drift risks. `cmru release` now owns context discovery, `PYTHONUNBUFFERED`, the aggregate
`cmru.release.log`, append separators, and the live tee. Retention is the default for logs,
declared artifacts, and declared gate evidence; explicit `--discard-logs-on-release`,
`--discard-artifacts-on-release`, and `--discard-evidence-on-release` opt out independently.
Evidence is declared separately from publishable artifacts because a coverage report or assay
verdict proves the gated commit but is not a release asset. The declaration is bounded to
project-relative files/directories and the transaction refuses missing or symlinked evidence
rather than guessing what the gate meant. Removing the wrapper
also removes it from CMRU's mutation and coverage input lists.

## Why CMRU pins Assay R1 to a release tag and splits R2 out

The release candidate is a snapshot of `origin/main`. Assay resolves a named
base against the tested commit; once a branch is merged, `main` can resolve to
the tested commit and leave R1 with no changed lines. CMRU therefore pins R1 to
the highest-version previously published ancestor release tag (`cmru-v5.5.0` in the current config), which
keeps changed-line coverage meaningful after merge. Advance this pinned base
to the newly tagged release on the next release candidate; keep the current
candidate pinned to the release before it. A rerun on the newly tagged HEAD
then excludes every CMRU release tag at HEAD and finds the highest-version
published release in the remaining ancestry, preserving the same baseline.
This selection examines all merge parents and breaks equal-distance histories
by the published release version, so `git describe`'s traversal tie cannot
choose an older first-parent tag. R2 is a
separate concern: the release
candidate is already at `origin/main`, so using `main` as its mutation base
would leave no mutation candidates. The dedicated mutation lane resolves the
highest-version published ancestor `cmru-v*` tag dynamically and mutates CMRU
source changed since that release. The tag is the highest-version published
release in the candidate's full ancestry. The registered `gate` lane queries all CMRU release tags on
origin in one host-side call using CMRU's credential-scoped Git transport, then
passes token-free facts for every published CMRU tag and its commit to the
dedicated `cmru-mutation` tester environment through
`CMRU_ASSAY_BASELINE_FACTS`. The mutation checker binds those facts to HEAD,
requires the selected tag to match the configured Assay R1 base and verifies
its origin commit. On an untagged candidate, that ancestor must also be the
latest published CMRU release. On a tagged-HEAD rerun, every release tag at
HEAD is excluded and the highest-version published ancestor remains the
previous baseline; the latest published tag may be one of the
verified tags at HEAD. It
also verifies every local CMRU release tag at HEAD against its exact origin
commit, including older tag names that point to the same commit. It
checks Assay's effective comparison commit too: if a merge's first parent is
after the tag, CMRU source roots must be unchanged across that gap so the two
lanes use the same source range. A missing tag or mismatch fails the gate. Run the
registered `./run-gate.py gate` lane to generate fresh origin facts immediately
before mutation. The tester independently selects the highest-version
published ancestor using Assay's sanitized ancestry API. If that source diff
is empty, the mutation lane writes explicit
skip evidence bound to the candidate HEAD. The serial campaign uses a
120-second timeout per candidate and stops each failed candidate at its first
failing test (`--maxfail=1`). Mutation outcomes live in
`.assay/mutation-cmru.json`; `.assay/progress-mutation-cmru.jsonl` is only the
append-only progress stream. Resume requires the exact test and copied-fixture
fingerprints. An added test can change pytest collection or install an autouse
fixture, so even an additive suite cannot reuse older kills. If a gate
is rerun after HEAD itself received one or more release tags, the checker
excludes every local CMRU release tag at HEAD and finds the highest-version
published tag in HEAD's remaining full ancestry, keeping the pinned R1 base
stable.

The mutation and coverage-canary controls use the same disposable CMRU test
closure. It includes the Topos and nyxloom CMRU manifests read by the estate
adoption contract test; otherwise the control could fail before exercising a
mutant and provide no valid R2 or canary evidence.

The full `run-gate.py gate` still covers R0 through R3: R0 runs the full test
suite, R1 requires 100% line-and-branch coverage, R2 runs the tag-based
changed-source campaign, and R3 runs an import-break canary. The gate also
retains total-coverage, cause-sensitive canary, and real-enrollment lanes.
The registered enrollment lane marks its fixture checks as required: absent Docker or gate-slice
prerequisites and a failed fixture-image build must fail the lane. Local standalone test runs may
skip the container oracle when Docker is unavailable.
The Assay lane uses the estate-approved `repository-minus-unsafe-symlinks`
snapshot and names the three tracked Topos fixture omissions explicitly; a
new unsafe symlink therefore fails closed. The selected worktree's Assay
source is installed at run time, so its verdict records the tool version.

### Keeping release credentials out of gate containers

CMRU copies its ignored root and selected-project secret overlays into a
release worktree so its host-side release transaction can use the configured
GitHub credential. The copy opens source files without following links, walks
candidate directories without following links, rejects nonregular destinations,
and atomically installs a mode-0600 sibling file. A symlink in a retained
candidate therefore cannot redirect credentials outside the worktree. A tester-unified container can read a mode-0600 file owned
by its mapped uid, even when its environment allowlist does not forward the
token. The registered host `gate` lane therefore resolves CMRU's scoped Git
auth first, saves any copied overlays in a private temporary directory outside
the mounted repository, and replaces their worktree paths with symlinks to the
host backups for the full lane sequence. Host CMRU processes can still follow
those links; tester containers see the same absolute `/tmp` paths in their own
filesystem, where the host backup directory is not mounted. Nested
`run-gate.py` processes strip publisher-token and extra-mount variables
(`RUN_GATE_EXTRA_MOUNTS`). The host uses the saved auth object to query origin
immediately before mutation and forwards only token-free tag and commit facts.
Masking moves the visible entry to a sibling, checks the inode actually moved,
then installs the backup symlink with an exclusive link operation. If an atomic
credential rotation lands at that boundary, CMRU keeps the moved entry or puts
it back without replacing a newer path entry, and the gate fails before running
the tester.
A `finally` path restores the original overlay bytes and file metadata even
when a registered lane fails, SIGTERM arrives, or SIGHUP arrives. If restoration fails, the gate reports and keeps
the private backup directory so the only saved secret copy is not deleted.
If a credential path was atomically replaced while the gate was running, restoration preserves
that replacement, fails the gate, and retains the original private backup for operator recovery.
The aggregate `gate` lane owns this boundary. Direct component-lane runs use
the mounted checkout as-is and require a secret-free checkout.

## Release history errors refuse the plan

CMRU uses `git log` to decide whether a registered project changed since its release tag.
An empty successful result means there are no matching commits; a nonzero result means CMRU
could not determine that fact. Returning an empty list on Git failure would silently skip the
project, so release planning preserves Git's diagnostic and refuses before any project cycle
starts. The `cmru status` and release paths share this fail-closed history reader.

## Candidate-first promotion protects the source history

### Dry-run external version discovery

An external version is a declared fact produced by a project's `steps.prepare`
command. A preview that skips that query cannot show the version it would tag,
which makes the preview less useful precisely for projects such as PWMCP whose
version is the intersection of several upstream registries. CMRU therefore runs
only the selected external-version preparation in the disposable release
candidate before computing the plan. It commits only declared generated paths
there; the caller checkout, gates, tags, builds, pushes, and promotion remain
untouched. Ordinary projects and non-external prepare steps retain the existing
dry-run behavior.

An isolated release pushes its transaction branch to origin as a durable candidate. Each
project is prepared and gated there, then its exact tag must be on origin before CMRU starts the
build or publisher. Consumers resolve release artifacts through the published source tag. After
a failed push, CMRU verifies the remote tag: a matching tag permits publication to continue; a
confirmed absent tag is removed locally and the pre-tag candidate remains resumable; an unknown
remote state retains the tag and candidate for inspection. CMRU fast-forwards `origin/main` from the same candidate only after
publication succeeds. This keeps a failed build or upload out of `main` and lets a later project
consume an earlier project's completed release in the same run.

The promotion is deliberately a single fast-forward push. CMRU does not rebase the candidate
when another writer advances `origin/main`, because that would change the SHA that was gated and
used to build the artifact. The candidate branch and worktree remain available for inspection;
success deletes the now-redundant branch. A version strategy that creates a mechanical version
commit receives a second gate on that exact commit before publication.
