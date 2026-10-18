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
document emitted by the installed `cmru`
dispatcher, including nested verbs. CMRU configuration diagnostics put the
identity first; `cli-extended` usage/refusal diagnostics put the actionable
message first and then render the matching generated help. Normal command output
is unchanged.

## One declared CLI grammar

The CMRU wheel installs one operator CLI, `cmru`. It uses `cli-extended` registrations as the source for
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
handlers. The [canonical CLI grammar and semantic
audit](SPEC.md#s-cli9-canonical-cli-grammar-and-semantic-audit) inventories their complete option surfaces
and is updated with every product grammar change.

### Project commands use the transaction's CMRU runtime

A project step that invokes `cmru` must run the same CMRU code that started the
transaction. Resolving through ambient `PATH` could select an older installed
wheel and run a different command contract inside a current source checkout.
Before each step, CMRU creates a temporary launcher using its current Python
interpreter and module root, puts that launcher first in `PATH`, and checks its
reported version against the active runtime. The internal `CMRU_INTERNAL_BIN` and `PATH` binding
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
boundary. The remote tag inventory is parsed as a complete protocol response:
malformed records, invalid Git refs, tags outside the requested pattern, or
orphaned peeled-tag rows stop cleanup before a destructive plan can treat the
omitted ref as absent. This keeps a parse failure from turning into a false
completion report.

`tester-gate --dry-run` does not run the host checks. Both the systemd slice check and
the optional IO-controller check need a temporary privileged container; starting one would
break the promise that dry-run starts nothing. The preview therefore shows the workload
command and any DinD startup command, prints that the host checks were skipped, and keeps
the `--cgroup-parent` argument visible. Actual execution performs the checks before it
starts the gate, and places every helper and workload container in the declared gates tier.
This makes the boundary honest: dry-run proves command construction, while a real launch
proves host acceptance.

Container fixtures that need the gates slice (ciu's real-enrollment lane, which moved out of
cmru with `get.py enroll`) use CMRU's Docker-host systemd probe to check that the configured
gates slice is loaded and fragment-backed before the fixture starts. A local `systemctl` query
can inspect the caller's namespace while Docker targets a different host. A nonempty name alone
can refer to a typo or an unconfigured transient slice, which Docker may accept without the
intended host placement.

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

The optional DinD sidecar is a separate container. It receives the same gates-slice parent and,
decided 2026-10-05 (BG-07), its own required memory, CPU and pids limits
(`CMRU_TESTER_DIND_MEMORY`/`_CPUS`/`_PIDS_LIMIT`): reusing the workload's limits would double the
per-step envelope, so the Docker-enabled path pays for three extra required inputs instead.

Process accounting (KI-52). The gate command is one cmru does not control, and git's detached
auto-maintenance orphans one process per commit; with the command as PID 1 nothing reaps them,
and on 2026-10-05 they filled the host-default pids ceiling (systemd `DefaultTasksMax`, not a
declared limit), which made assay record false kills. So the workload and the sidecar run under
`--init`, the workload gets a required `--pids-limit`, and, because `--rm` removes the cgroup at
exit, an in-container wrapper copies the container's own `pids.events`/`memory.events` to a
cmru-owned file on the mounted worktree. A non-zero `pids.events max` or `memory.events
oom_kill`, or a missing file, is an infrastructure failure (exit 3), never a pass.

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
interface: `cmru`. It uses
`cli-extended` to define grammar, options, help, and dispatch. CMRU does not
maintain parallel hand-written parsers for that command.

`python -m cmru.handlers` is bootstrap-only: `build-initial-standalone.sh`
needs it to build the first wheel before the installed `cmru` command exists.
Project step contracts invoke registered artifact handlers as
`cmru handler <verb>`, which inside a release transaction resolves to the
bound launcher (`cmru standards` flags the module form). `python -m cmru.bundle`, `python -m
cmru.runner`, and module aliases for the three operator scripts are retired;
they now refuse with a pointer to the supported command or library API.

The first CMRU wheel can be built before either CMRU or `cli-extended` has a
release. `build-initial-standalone.sh` has an explicit source mode, selected with
`CMRU_BOOTSTRAP_CLI_EXTENDED=source`, that builds
`cli-extended` from the same checkout at CMRU's declared runtime floor and
marks it `+bootstrap.source`. The standalone wheel-builder uses Microsoft's
upstream Python base, so this bootstrap does not depend on MDT's published
image. The bootstrap verifies the builder image's dedicated label and
rebuilds a missing or mismatched tag from this Dockerfile, rather than
mistaking an unrelated local tag for the builder. The operator supplies a temporary local
version (`0.0.0+bootstrap.source`) for the bootstrap controller whether or not the checkout has a CMRU tag;
that version is not a release tag or published artifact. The wheel builder bind-mounts the
LIVE working tree, but the script's identity is HEAD (its derived version and
`SOURCE_DATE_EPOCH`), and this wheel becomes the controller of later releases. Building from an
exact-OID snapshot would need a second source path through `wheel-build`; the smaller safe
contract is a refusal: before any Docker invocation, the host probe included, the script
requires that tracked edits, untracked files and Git-ignored files that setuptools would still
package (it ignores `.gitignore` when packaging a source root) are absent under
`cmru/pyproject.toml`, `cmru/src` and `libraries/worktree/src` and, only in source mode, the
`cli-extended` library, and an unreadable Git state is a refusal rather than "clean". Unrelated
dirt elsewhere in the checkout does not block it, and `wheel-build` itself is unchanged. The
script builds and records the
two wheels but installs nothing: the guide's first step installs them and puts the owning
environment first on `PATH` before the first `cmru` command, so the procedure never relies on a
script-installed wheel. Why the slice is proven first: systemd auto-creates an unlimited
transient slice for any unknown name and Docker then starts the build there without complaint,
so both the script and `cmru handler wheel-build` read the Docker host's systemd through the
pinned probe image for the exact slice they pass to Docker (after proving the gates slice that
hosts that probe), and refuse on an unloaded, typo'd, missing or unprobeable answer; there is no
fallback slice and no unverified path. The graph keeps actual package and release-gate prerequisites separate
from the source-built CMRU controller used to run other projects' release
commands. See the [zero-release procedure](../../docs/BOOTSTRAP-FROM-ZERO.md).

The reusable `cmru.bundle.run_bundle` and `cmru.runner.run_step` functions
remain supported Python APIs. PWMCP consumes the bundle API, and MDT's
`build-push.py` consumes the runner API. Use `cmru run --step NAME` for a direct operator invocation. CMRU does
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

### Direct wheel-build uses the declared image

`cmru release` injects each project's configured environment before running a build step.
An operator can also invoke the supported `cmru handler wheel-build` verb directly before
building a gate image. The handler therefore resolves `CMRU_WHEEL_BUILDER_IMAGE` from an
explicit environment value or the nearest CMRU orchestration/project configuration enclosing
the selected project directory. It does not invent `wheel-builder:local` when configuration
is absent. This keeps bootstrap builds on the same declared builder as release builds while
making an unconfigured project fail before Docker starts.

### Wheel-build proves its slice on the host first

systemd auto-vivifies a `.slice` for any name, so Docker given a typo'd `--cgroup-parent`
starts the build normally inside an unlimited transient slice beside production. A non-empty
string is therefore not placement. `wheel-build` (and the bootstrap script, before its
Buildx) runs the same host-side probe `tester-gate` uses
(`cmru.tester_gate.check_slice_unit`: `LoadState=loaded` plus a non-empty `FragmentPath`,
read from the Docker host's systemd by a pinned privileged `nsenter` container, never a
local `systemctl` from a cockpit that cannot see the host's) before any builder workload.
Rejected: probing inside the slice under test (the probe would itself be the first
unverified workload) and a cockpit-local filesystem/`systemctl` check (asks the wrong
kernel). The probe is placed in the already-declared gates tier
(`CMRU_TESTER_CGROUP_PARENT`), its image is the declared digest-pinned
`CMRU_TESTER_CGROUP_PROBE_IMAGE`; either input missing, or any uncertainty, refuses. The
probe parent is itself verified first with `tester-gate`'s own self-check
(`check_slice_unit(parent, image, parent)`: a short, read-only probe placed in the slice it
asks about; a typo'd name is still caught because it reads an empty `FragmentPath`), and
only an affirmative answer lets it host the probe of the build slice. A typo'd gates tier
therefore refuses naming `CMRU_TESTER_CGROUP_PARENT` with no builder started, instead of
silently hosting every probe. This adds no public input.

### Direct publish binds notes to the published tag

`publish --from-checkout` reads release prose from the local `<prefix>-v<version>` tag. A
tag that merely shares the published name (moved, re-created, or never pushed) would put
another commit's notes into an immutable release body, so the handler reuses CMRU's exact
origin comparison (`cmru.version._tag_pushed_to_origin`, direct and peeled refs, so annotated
and lightweight tags compare by the commit they target) and refuses before any publish call
when the tag is absent from origin, disagrees with it, or origin cannot be queried. An
inaccessible origin is never read as "nothing released". The isolated `cmru release` flow
names its commit explicitly and is unchanged.

### Retrying a tagged OCI release body runs only the metadata handler

An OCI release can push its image successfully and then fail while creating or updating the
GitHub Release body. Replaying the full `push` step would rebuild and push the image again,
which could replace the public version tag if its build inputs changed. `cmru publish
--from-checkout --release-tag TAG` is the narrow retry: it requires one tagged project with a
configured `oci-image-release` command, a clean checkout whose `HEAD` matches the explicit
local tag, and—on a real run—the same peeled commit at origin. It passes that tag and commit
as protected identity and executes only the validated final body handler, in the push step's
declared environment (`env`, `env_command`, `required_env`) so it targets what the original
release would have; the step's registry login and `clean_dirs` belong to the image push and are
not repeated. An origin that cannot be reached or returns a malformed ref advertisement is a
failure, never read as "tag absent". The handler again
checks GitHub's tag and release target immediately before writing and reads the result back.
`--dry-run` checks local identity and displays that single command without querying origin.

The explicit tag prevents a fallback to whichever tag happens to be at `HEAD`; the clean
checkout binds the configured command to the inspected source. The retry never builds or
pushes an image, so it cannot silently replace the image whose version was already published.
See the [pasteable consumer example](CONSUMERS.md#retry-a-tagged-oci-release-body).

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

### Tagged artifact identity binds builds to CMRU's candidate

The artifact command needs the same source identity as the CMRU release
transaction.

For tagged release artifact steps, CMRU supplies `CMRU_RELEASE_COMMIT` as the
exact gated candidate commit and `CMRU_RELEASE_TAG` as its tag. It clears both
ambient values from every other project step, and project and step configuration
cannot override these protected values. The pair is source-consistency metadata,
not an authentication token or proof of CMRU provenance.

The consumer checks its current `HEAD` and local tag against that pair before
building. Reading a matching tag name alone is insufficient: a clean checkout can still carry a
moved local tag, and a separately-read revision can drift from the version
lookup. Publication must use the supported `cmru release` workflow.
The values are reserved so project configuration cannot replace the identity
CMRU supplies to tagged artifact steps.

The release gate also receives the selected commit as the protected
`CMRU_RELEASE_GATE_COMMIT`. This lets a package prepare a commit-bound test
receipt before CMRU assigns a release tag. CMRU clears ambient values of this
name on ordinary steps and refuses project configuration that tries to set it.

CMRU also rechecks the candidate source around every gate, build, and publish
step. `git status` alone is insufficient because `assume-unchanged` and
`skip-worktree` index flags can hide modified files. CMRU refuses those flags
and compares tracked file bytes and modes with the candidate commit before it
trusts a step; ignored logs and generated artifacts remain permitted.

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
declared artifacts, and declared gate evidence; explicit `release --discard logs`, `--discard artifacts`, and
`--discard evidence` (repeatable) opt out independently.
Evidence is declared separately from publishable artifacts because a coverage report or assay
verdict proves the gated commit but is not a release asset. The declaration is bounded to
project-relative files/directories and the transaction refuses missing or symlinked evidence
rather than guessing what the gate meant. Removing the wrapper
also removes it from CMRU's mutation and coverage input lists.

## Why CMRU pins Assay R1 to a release tag and splits R2 out

The release candidate is a snapshot of `origin/main`. Assay resolves a named
base against the tested commit; once a branch is merged, `main` can resolve to
the tested commit and leave R1 with no changed lines. CMRU therefore pins R1 to
the highest-version previously published ancestor release tag (`cmru-v6.1.0` in the current config), which
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
registered `run-gate gate` lane to generate fresh origin facts immediately
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

An operator may explicitly postpone the long R2 campaign for a provisional
release. The `gate-provisional` lane calls the same aggregate driver with a
tracking id; the driver still runs the protected-ref probe, installed-wheel
acceptance, Assay R0/R1/R3, total coverage, and canary. It records the skipped
mutation lane with the candidate commit, time, reason, and tracking id under
`.assay/mutation-postponed-cmru.json`. The full `gate` remains separate and
continues to run R2. A provisional marker is evidence of a deferral, never a
mutation pass: the marker and the snapshot manifest's `deferred_lanes` are written only after
every required non-mutation lane and the final canary succeeded (so a failed canary can leave
no success-shaped record), the manifest `state` is `provisional` rather than `passed`, and a
valid new gate start deletes any earlier marker, while an invocation refused by input
validation (before a new gate starts) preserves the previous run's evidence. Success is decided
only after snapshot cleanup: a cleanup failure after the lanes passed rewrites the manifest as
`failed`, removes the marker and fails the gate, because the canary-replaced signal is exactly
what cleanup exists to catch. The recorded follow-up is the full `gate`, not the standalone
`mutation` lane, which cannot run without gate-prepared baseline facts. Rejected: keeping
`state: "passed"` and relying on the marker alone (a reader of the manifest would see an
unqualified pass). The native-R2 mutation lane declares contract-2 event
observation; the current command lanes declare resource ceilings but do not
collect event evidence. Declaring an observer on a lane with no observer client
would add a daemon dependency without detecting OOM or process-limit events, so
KI-77 tracks actual event capture for those lanes before they opt in.

### First release has no historical diff baseline

A first stable release has no previous release commit to use for changed-source
R1 or tag-based R2. The gate queries origin through CMRU's credential-scoped
Git transport and distinguishes a successful empty stable-tag list from a
failed query. Only the former selects the registered whole-source
`cmru-first-release` Assay lane, and only inside the protected CMRU release
transaction whose candidate SHA matches HEAD. The lane names every tracked
production module explicitly and is checked against the source tree, so an
added module cannot silently fall outside the claim. The gate does not invent
a baseline from `main`, a local-only tag, or another project's release.

The first-release R2 campaign remains a separate, explicit whole-source
follow-up. The release marker points to `run-gate assay-first-release-r2`, which
uses the same declared source list and does not need a previous tag. This keeps
the release claim honest while allowing the long mutation campaign to run
after the first stable tag exists. Once a stable CMRU release is published,
subsequent release candidates return to the normal pinned R1 and tag-based R2
paths.

#### Mutation process-tree isolation

Each mutation command starts in a fresh process group while CMRU's host
campaign process temporarily acts as a Linux child subreaper. Process-group
signaling is fast, but cannot reach descendants that call `setsid()` or
double-fork. After waiting for the command leader, CMRU discovers its adopted
direct children, signals and reaps those exact PIDs, and proves the child set
empty before taking after-counters or starting another candidate. It never
uses `waitpid(-1)`, which could consume an unrelated child's status in an
embedding process.

A normal leader exit with descendants becomes `crashed` after successful
cleanup; the campaign continues, and the attempt cannot count as killed or
survived. Cleanup failures stop the campaign as infrastructure errors.
Timeouts and cgroup resource events also remain inconclusive. The schema-5
resume record identifies `candidate_isolation = "process-tree/1"` and stores
per-candidate process-tree evidence. A killed result is reusable only when its
leader exited normally, no descendants outlived it, the timeout is unchanged,
and resource counters show no limit event. Changing the timeout starts a fresh
campaign because it changes whether a candidate could have completed. Earlier
schemas are refused so they cannot silently reuse a result without those proofs.

Resume also binds the Assay implementation that runs the campaign. CMRU derives
the shipped package roots from Assay's `pyproject.toml` and uses a sanitized Git
subprocess to establish cleanliness before importing Assay. That avoids using
the code being checked to certify its own clean state. The imported package must
resolve inside a declared root. Unsupported layouts such as `py-modules` or
named `package-dir` mappings, and package roots containing symlinks, refuse
closed until CMRU can bind their
imports precisely. After the baseline control, CMRU checks source cleanliness
and both repository HEADs again so edits during that control cannot be hidden.
The source commit is the exact Assay identity; `assay_distribution_version` is
only the package metadata label and may not describe an unbuilt source edit.
This covers packages such as `assay_analysis` under `analysis/src/`, not only
the CLI package under `src/assay/`.
The registered mutation, coverage, and canary lanes also run a source preflight
before editable installation, checking the whole Assay checkout for stray build
hooks or other uncommitted inputs. It permits identified generated state such as
bytecode, pytest and Hypothesis caches, and coverage outputs; ignored source
files and build hooks still refuse. CMRU copies only tracked project inputs into
one private fixture template; the baseline and each candidate use fresh copies
of that exact snapshot. Run-Gate bookkeeping, Assay state, and coverage outputs
never enter a candidate fixture. Both the controller and child test processes
use private bytecode caches, so an old ignored `.pyc` cannot substitute for the
checked source.
The history comparison runs from Assay's own Git worktree. That matters when
CMRU's campaign repository and the selected Assay source are different linked
worktrees: a path under `.worktrees/` is not part of the main checkout's
committed tree. Package discovery and root-level package-directory mappings
are derived from `pyproject.toml`; named package-directory mappings and
unsupported external data-file mappings refuse instead of creating a partial
source identity. Ignored files under the bound roots or mutation test closure
also refuse, apart from generated bytecode/pytest caches, coverage data, and
editable-install metadata. Assume-unchanged and skip-worktree index flags also
refuse.

The mutation and coverage-canary controls use the same disposable CMRU test
closure. It includes the Topos and nyxloom CMRU manifests read by the estate
adoption contract test; otherwise the control could fail before exercising a
mutant and provide no valid R2 or canary evidence.

The full `run-gate gate` still covers R0 through R3: R0 runs the full test
suite, R1 requires 100% line-and-branch coverage, R2 runs the tag-based
changed-source campaign, and R3 runs an import-break canary. The gate also
retains total-coverage and cause-sensitive canary lanes. (The real-enrollment lane moved to
ciu's `run-gate.toml` with `get.py enroll`.)
The Assay lane uses the estate-approved `repository-minus-unsafe-symlinks`
snapshot and names the three tracked Topos fixture omissions explicitly; a
new unsafe symlink therefore fails closed. The selected worktree's Assay
source is installed at run time, so its verdict records the tool version.

### Keeping release credentials out of gate containers

**No credential file is ever copied into, or read from, a release worktree.**
`cmru release` (and `cmru build`) runs on the host and resolves the credential
there, exactly as any other cmru invocation does: the invocation environment
token wins, else the root `cmru.secret.toml` merged with the selected project's
overlay, read from the caller's checkout (or the central CMRU root). The
transaction child, which runs from the isolated release worktree, receives the
already-resolved root token and per-project tokens from its parent over a private
inherited pipe (`CMRU_INTERNAL_CREDENTIAL_FD` carries only the descriptor
number). The token is never placed on argv, never written to disk, and never
exported through the environment. The child reads the pipe once, closes it,
and exports only a `CMRU_INTERNAL_CREDENTIAL_STATE=consumed` marker. A resumed
child (`--resume WORKTREE`) is launched the same way, so the credential never
depends on what a retained worktree contains.

Inside a transaction child the config loader NEVER opens a `cmru.secret.toml`
(root or project) and ignores ambient `GITHUB_PUSH_PAT`/`GITHUB_TOKEN`; a child
that received no handoff fails closed with a configuration error instead of
falling back to its own root. A process nested below a consuming child (a project
step that re-invokes `cmru`) sees only the environment token, never a file.
(This package replaces the former copy step, which opened source files without
following links and installed mode-0600 siblings in the worktree: a tester
container can read a mode-0600 file owned by its mapped uid even when its
environment allowlist does not forward the token, so the safest copy is none.)

### Aggregate gate binds its source and judge

The aggregate release gate requires a linked Git worktree with no tracked
changes. It creates a stable, attached sidecar worktree at that exact commit
through the shared worktree lifecycle, then runs each registered lane from the
sidecar. The caller's checkout is never mounted into tester-unified. This
removes the inventory race: a publisher secret already present in an ignored
directory, or created in the caller after snapshot creation, is outside the
tester-visible tree rather than temporarily masked. A random host-only canary
is planted in the caller and the `installed-wheel-live` lane verifies from inside
the real tester container that the canary path is absent. That proof is bound to
THIS gate rather than to "a file says running": the manifest must name this very
snapshot checkout, be in state `running`, and have been refreshed within 30
minutes (the aggregate gate rewrites it before every lane). A `running` manifest
left behind by a killed gate fails the second and third test, so it can never
satisfy `--require-live-snapshot`. The plain `installed-wheel` lane carries no
such requirement and stays directly runnable, as its documentation says;
`installed-wheel-live` is the aggregate gate's variant and is not meant for
direct use.

The gate also judges only code bound to the claimed commit. The helpers it
imports come from the caller's mutable tree, so a gate that "judges commit X"
could be steered by a file that is not in X. Before anything imports or runs,
CMRU inventories every directory it puts on `sys.path` (`cmru/src`,
`cmru/tools`, `assay/src`, `assay/analysis/src`, `assay/tools`,
`libraries/worktree/src`) against `git ls-tree HEAD` and refuses: an untracked or
ignored executable module (including a top-level shadow such as `json.py`), any
symlink, and any tracked file whose blob differs from HEAD or was deleted. The
comparison is by blob hash, not by `git status --untracked-files=no`, which by
construction cannot see an untracked file. The check runs again before every
lane. (The simpler alternative, executing the orchestrator from the snapshot
itself, was considered; it needs a trusted bootstrap in the caller to start it,
so this change keeps the host-side runner and binds it to HEAD instead.)

The gate protects its own refs and guards release anchors in the shared ref
store. A
digest of every ref (hundreds on a busy estate) made any other agent's commit,
tag or fetch during a 14-hour gate a false failure that blamed the tester lane.
The protected Git refs are the snapshot's branch, the source branch being judged,
and the gate-owned `refs/cmru-gate/<id>/` namespace; a lane that moves one is a
failure, and everything else in the repository belongs to concurrent work that a
gate cannot attribute to a tester. Because the detector must be proven rather
than assumed, the aggregate gate first runs the `ref-probe` lane: from inside the
real tester container it writes a ref in that namespace, and the gate must
detect it (then removes it). A silent detector fails the gate. Limit, stated
plainly: a tester that writes an unrelated shared ref is not distinguishable from
another agent doing so. CMRU separately records existing release tags and
`refs/heads/main`: a lane cannot move or delete an existing tag, and `main` may
advance only by fast-forward. New refs and unrelated branch refs are outside this
check. Run-Gate keeps the common Git directory writable, so these checks detect
the protected changes after a lane; the remaining shared-ref surface is tracked
in [KI-77](../KNOWN_ISSUES_TODO_BACKLOG.md#ki-77--the-release-gate-lacks-container-resource-event-evidence-on-non-mutation-lanes-and-cannot-confine-every-shared-git-ref).

Snapshot leases are judged by evidence, never by a stat of an absent PID. A
holder records its PID, process start ticks, and its PID-namespace and boot
identity. A lease is `dead` only when (a) there is none, (b) it expired and its
holder is not a CMRU gate, or (c) the holder is from THIS namespace and its
process is provably gone or recycled. A holder from another namespace (a host
PID seen from the devcontainer), an unreadable one, or a legacy format is `live`
while its 18-hour TTL runs and `indeterminate` after it; neither is ever reaped.
The same rule applies to the source checkout: a path this process cannot stat
is not a gone source, because it may live in another mount namespace (the
estate's own CIU-15 lesson). Absence counts only with Git's evidence: the source
is no longer registered, or registered, unlocked and reported prunable. The
shared worktree library enforces that for orphan removal too
(`remove_orphan_workspace` now refuses a source Git still registers as live).

`cmru gate-snapshots` is the supported way to see and remove retained snapshots
(`cmru release`'s own cleanup removes its candidate's). It lists each snapshot
as `removable` or `retained` with the reason, re-judges every candidate from
fresh facts at removal, and is safe to repeat. A snapshot whose source is still
at its commit is retained for resume unless `--include-current` says otherwise.

Before allocation, CMRU refuses a tracked `cmru.secret.toml`, a configured Git
filter that could execute during checkout, and tracked files at the paths
reserved for gate evidence. Lane output is checked before it is trusted or
cleaned: tracked changes, unexpected ignored/untracked files, unsafe links, or
oversized Run-Gate state fail the aggregate gate. Between lanes CMRU restores
the sidecar to the exact commit, retains only its `.run-gate/` resume state,
and reseeds a fixed evidence allowlist. Only those named evidence files are
copied to the caller. The attached sidecar and its branch remain available for
resume; successful release-candidate cleanup removes the sidecar through the
same worktree lifecycle.

Run-Gate RG-NARROW mounts the judged worktree plus selected Git metadata. The
shared Git directory must remain read-write for in-lane Git operations, so
Run-Gate overlays its common `hooks/` directory with a private empty read-only
directory and mounts `info/` read-only. This prevents a tester from planting a
host hook for a later host commit or changing host exclude metadata. CMRU also
disables host Git hooks while it prepares remote baseline facts. CMRU starts its
host release-gate driver with isolated Python (`-I -B`) so an untracked module
beside `run_release_gate.py` cannot run before the source integrity check. The
driver starts lane processes through the installed `run-gate` CLI, not a script
from the mutable checkout. Before any sub-lane starts, CMRU requires a stable Run-Gate wheel at SemVer
`24.0.0` or later. It rejects development and local suffixes, verifies the
CLI's reported SemVer against wheel metadata, and requires a non-editable wheel
install. It resolves the release for the installed version, downloads and
hashes that exact published Run-Gate wheel, compares the digest with the
installed wheel's `direct_url.json` archive digest, and compares both installed
executable modules with the copies inside that wheel. It checks the configured
`tester-unified` image's Run-Gate version and wheel-SHA labels against those
published bytes. This permits an older but still supported stable release when
it is the wheel actually installed. The image ID and labels are rechecked
around every lane, and every lane is pinned to the inspected image ID. The
snapshot records the launcher, installed distribution,
published wheel, and tester image identities; a resumed run refuses if any
changed. This closes the gap where a stable-looking local wheel or a stale
`tester-unified:local` image could pass a version-only check.
Nested `run-gate` processes receive neither
publisher-token nor extra-mount variables (`RUN_GATE_EXTRA_MOUNTS`); origin
facts passed to mutation contain tag names and commit IDs, not credentials.

The host gate resolves the scoped Git auth from the caller through the private
credential handoff described above; no secret file enters the sidecar. The
aggregate `gate` lane owns this boundary. An individual tester component lane
bypasses snapshotting and should use a secret-free checkout. KI-64/65/73's masking and
restoration requirements are superseded by the exact-snapshot design tracked
in KI-74; their original incidents remain in the backlog for history.

## The bare-host gate controller runs in the installed CMRU environment (KI-81)

`gate` and `gate-provisional` are `bare-host` lanes: the controller must reach the
Docker daemon to start each `tester-unified` lane container itself, so it cannot itself
run in one. Its command is `python3 -I -B .../run_release_gate.py`, and on a real host
`python3` is simply whatever `PATH` names, for example the system interpreter, which has
no `cli-extended`. The first exact-tip gate therefore passed its commit-binding preflight
and then died with `No module named cli_extended` before running anything. The controller
imports CMRU from the exact worktree source (that is the code being judged) but, like every
CMRU consumer, takes the *declared third-party wheels* from an installed environment; it
never vendors them or puts the library's source on a path (CX-D1, pinned by
`tests/test_packaging_cli_extended.py`).

The choices, and what was rejected:

- **Derive the environment from CMRU, not from a path.** A release already requires a
  non-editable installed `cmru` wheel, so the environment exists and CMRU knows it. Inside
  `cmru release` CMRU states it: `run_project_step` hands every step the protected
  `CMRU_INTERNAL_PYTHON`, the running cmru's own `sys.executable`. A console-script shebang
  lookup cannot do this there, because the first `cmru` on a step's `PATH` is CMRU's bound
  launcher, a `/bin/sh` wrapper that execs that interpreter: it names a shell, and reading
  the wrapper's second line would couple the gate to a private file format. The variable
  sits in the already reserved `CMRU_INTERNAL_` namespace, so project `[env]` cannot declare
  it and it is applied after project and step environment. The controller honours it only
  together with the protected `CMRU_RELEASE_GATE_COMMIT` that the same workflow sets for the
  same step: an operator's stale shell variable must not choose the interpreter of a manual
  run (the rule `CMRU_INTERNAL_BIN` already follows), and an unusable protected value is a
  refusal, not a cue to search `PATH`. For a manual `run-gate gate-provisional` there is no
  CMRU process to ask, so the installed `cmru` console script's shebang, which names the
  environment the wheel was installed into, is read instead (with the parser already used to
  identify Run-Gate); a `cmru` on `PATH` that is not a Python console script is refused with
  an explanation. Rejected: writing the estate venv path in `run-gate.toml` (it is wrong on every
  other host, which is the failure class that `AGENTS.md` calls a shadowing default);
  vendoring `cli_extended` or adding its source to `sys.path` (a second copy that drifts
  from the declared floor); falling back to a weaker import path (the gate would then judge
  with different code than a release uses).
- **The declared floor is checked from installed metadata, not from importability.** The
  gate controller used to stop at `find_spec`, so a host that imported `cli_extended`
  0.2.0 ran the protected gate under a wheel older than CMRU's declared `cli-extended>=0.3.0`.
  It now reads each dependency's installed distribution version and tests it against the whole
  declared requirement (`>=`, `>`, `<=`, `<`, `==`, `!=`, evaluated by a small stdlib ordering,
  because nothing from the worktree or a dependency may be imported before the interpreter is
  chosen; a specifier it cannot evaluate refuses instead of passing). An importable module with
  no installed distribution metadata does not count. The authoritative interpreter is probed
  for the same version before the switch, so a below-floor CMRU environment, or a below-floor
  runtime-reported interpreter, is refused with the remedy instead of becoming the judge.
- **Switch only when needed, once, and loudly.** If the start interpreter already
  satisfies every declared requirement nothing changes, so a correctly set-up host and the
  tester suite see no difference. Otherwise the controller probes the launcher's
  interpreter for the same imports, prints which interpreter it is leaving and entering, and
  starts the same script once more with the lane's own `-I -B` and the original arguments. A
  one-shot marker, removed before any lane starts, turns a second failure into an error
  instead of a loop. This is selection of the authoritative environment, not a degraded mode:
  every outcome either runs the full gate or stops.
- **Dependency names come from `pyproject.toml`.** The set to check is read from
  `[project].dependencies` (distribution name normalised to its import name), so adding a
  declared dependency needs no change to the script and the script names no library.
  Reading that one file as data is the only worktree access before the source-integrity
  check; the script itself is stdlib-only and starts again unchanged, so the property that
  an untracked module beside it cannot run first still holds.
- **Fail with the remedy, not a traceback.** A missing `cmru` launcher, an unusable
  shebang, an interpreter that also lacks the dependency, or an unreadable declaration is a
  `cmru-release-gate:` error (exit 1) naming the interpreter and launcher, and telling the
  operator to install the released wheel with its dependencies and put that environment's
  `bin` on `PATH`.
- **Why not a Run-Gate feature.** Letting a lane declare its interpreter (a `{python}`
  token or an `interpreter =` key) would be the principled long-term home, but it is a
  public Run-Gate interface change with its own versioning, so it is recorded in KI-81 as a
  decision for the operator rather than made here.

None of this changes where tests run: the lane processes the controller starts are still
`run-gate <lane>` containers in `tester-unified`, the secret and exact-snapshot boundaries
above are untouched (the environment passes through unchanged), and the controller
remains a cockpit-side orchestrator, never a place to execute the suite.

## Release bodies carry the version's curated notes

A GitHub Release body used to be `<operator notes or "<prefix> <version>">` plus the
artifact block, and nothing read `CHANGES.md`: readers of `assay-v9.0.0` got install
text but not the change list or migration notes, and the changelog preflight (which
accepts a cleared `[Unreleased]`) could pass while the notes were silently absent.
The body is now the generated block followed by the project's own section for
exactly that version, taken from the same changelog the release already commits
and gates.

The choices, and what was rejected:

- **One builder.** `release.build_release_body` (and
  `build_variants_release_body`) assemble the body; the publisher and
  `cmru release --dry-run` both call them, so the preview cannot describe text the
  publisher will not send. A preview that re-implemented the layout would drift the
  first time one side changed. The only unknowns before a build, the artifact file
  name and its SHA256, are printed as marked placeholders rather than invented, between
  `----- begin release body -----` and `----- end release body -----` lines.
- **Exact section, verbatim, last.** The `## [X.Y.Z] - YYYY-MM-DD` heading through
  the line before the next `## ` heading, unmodified, so Markdown, code fences and
  migration steps survive byte for byte. It follows the install block because that
  is what a reader needs first. Other versions are never read, and `[Unreleased]` is
  never a *substitute* for the section (see the next item for what it is).
- **Fold `[Unreleased]` into every tagged candidate before the gate (KI-78).** The original
  KI-30 refusal left migration guidance outside the release record. CMRU now moves
  pending notes into the dated version section in the disposable candidate, before
  the gate and tag. A CMRU GitHub Release-body handler appends that exact tagged section
  to its generated body, so the tag and body share one immutable notes source. A
  `--no-build` release, or an image push without `oci-image-release`, still carries the
  notes in the tagged changelog but has no GitHub Release body. There is no post-publish changelog commit that can fail
  after a release is already public. Comment detection follows CommonMark code spans as well
  as fences: a changelog that documents CMRU's own `<!-- cmru: ... -->` markers quotes them,
  and blanking or hoisting a quoted marker would silently rewrite immutable notes.
  A failed gate or build leaves the candidate's staged section on its retained branch;
  the source branch remains unchanged. A failed publish also leaves source `[Unreleased]`
  unchanged, while the tagged candidate retains the exact notes sent to the publisher.
  Promotion carries the staged changelog to `main` only after the tagged transaction
  succeeds, including a successful publish when the configured transaction publishes an
  artifact. A dry
  run stages the same section temporarily and restores the original bytes; when no body
  will be published, it previews the tagged changelog section. Rejected: a post-publish
  changelog commit, which could block
  promotion after the body and assets were already public; a second changelog file,
  which would give authors another place to forget migration notes. Changelog paths
  remain excluded from project change detection, including custom names.
- **No silent fallback.** A missing, undated (`UNRELEASED`), duplicated, wrong-version
  or empty section refuses. The alternative, publishing the generic body, is the
  exact failure this change exists to remove. A project that declares
  `changelog = false` has decided it has no notes and gets the generated body
  alone; the two cases are distinguished by a protected declaration
  (`CMRU_RELEASE_NOTES_SOURCE=changelog:<path>` or `none`) that the runner gives the
  publishing step and projects cannot set. A publisher invoked without it refuses
  and names the remedy instead of guessing.
- **Refuse early, read the right bytes.** The prepare phase resolves the pending
  section after generating history and folding eligible `[Unreleased]` notes, before
  the gate, tag or upload. At publish time the text is read from the tagged candidate's
  commit (`CMRU_RELEASE_COMMIT`, a retained build's source commit, or the matching local
  release tag for explicit `publish --from-checkout`), not from later working-tree edits.
- **`--notes-env` stays, as a supplement.** It replaces only the opening line; it
  cannot supply or suppress the section.
- **KI-68 protects the notes.** Curated notes live in the generated section. If
  later project commits make the section stale, regenerating would replace its body
  and lose them, so CMRU now refuses (exit 4) when the old body holds lines its own
  generator would not produce, and lists them. Advance the section's
  `<!-- cmru: source-end=... -->` to the final project commit in the same commit as
  the edit and a current section is kept byte-identical. There is deliberately no
  override flag.

### Image-only releases get a body through `oci-image-release` (KI-78)

An OCI-image project's immutable artifact is a registry image its own build step
pushes; CMRU uploads nothing to GitHub, so the wheel/tarball body (artifact name,
SHA256, `sha256sum -c`) has nothing true to say, and the first KI-78 change left such a
release with no body at all. `cmru handler oci-image-release` publishes one, after the
project's own image push. The choices, and what was rejected:

- **Metadata only, never the image.** The handler neither builds nor pushes; the project
  puts it after its push command. A handler that pushed would own registry credentials
  and one more failure mode, and a release body is worth publishing only for an image
  that already exists. Retries are therefore cheap: the only writes are one create or one
  name/body update, and no asset is ever listed, deleted or uploaded, so a retry cannot
  disturb an asset that happens to share a name with something a wheel handler uploads.
- **The registry must be written explicitly.** `--image` is `<registry>/<repository>` and the
  pull instruction repeats it verbatim, so the registry has to mean the same thing to CMRU
  and to Docker. Docker treats the first path component as a registry only when it contains
  a `.` or a `:port` or is exactly `localhost`; otherwise `registry/team` is a Docker Hub
  namespace. CMRU applies that same test and refuses the ambiguous form with the fix
  (`ghcr.io/<image>` or `docker.io/<image>`), instead of publishing an instruction that pulls a
  different image than the one released. Accepted: `ghcr.io/volkb79-2/cgprofile`,
  `registry.example.com/team/app`, `myhost:5000/app`, `127.0.0.1:5000/app`, `localhost/app`,
  `localhost:5000/team/app`; the repository part is lower-case `a-z0-9` runs joined by `.`,
  `_`, `__` or `-`. IPv6 bracket hosts are not accepted.
- **The version comes from the protected tag, not from configuration.** `--image` is the
  repository only (`ghcr.io/acme/demo`), and `:<version>` is derived from
  `CMRU_RELEASE_TAG` (`<prefix>-v<version>`). A `--image` that already carries a tag or
  digest refuses, because it would be a second, unverifiable source of the version. The
  tag and version are validated before anything is built from them; a dev version, a tag
  for another prefix, or a value that is not a valid image tag refuses.
- **One builder, no placeholders.** `release.build_oci_image_release_body` opens with the
  exact `<image>:<version>` and a `docker pull` command, then appends the same tagged
  changelog section as every other body. Everything it prints is known before the build
  (no artifact file, no digest), so the dry-run preview is the complete body, not a body
  with marked blanks, and the handler and `cmru release --dry-run` call the same function.
- **Bound to the protected identity, verified against GitHub.** The notes are read from
  `CMRU_RELEASE_COMMIT`, never the working tree. That pair is source-consistency metadata,
  not proof of provenance (README), so before any write the handler asks GitHub for the tag
  and refuses unless it exists and points at exactly that commit. It never creates a Git ref:
  `cmru release` pushes the release tag before the `push` step, so an absent tag means the
  handler is running outside that ordering, and inventing one would be a fallback that
  substitutes for a fact CMRU already owns. A missing Release is created under that tag and
  commit; an existing one is updated in place after the same checks (and refused if it
  targets another commit).
- **What the verification can and cannot promise (a deliberate limit).** For an EXISTING
  release the handler re-fetches it and re-verifies its exact release ID, tag identity,
  commit target (when `target_commitish` is a full SHA) and remote tag target immediately
  before the PATCH; if any changed, nothing is sent. GitHub offers no conditional update and
  no way to make a tag check atomic with a release create, so the protection that remains
  is a read-back after EVERY write, create included: the release is fetched again and must
  carry the positive ID the write returned, the exact tag and a body equal to the intended
  body, and the remote tag must still resolve to the release commit. Trusting the write's
  own response would let a release replaced right after the POST, or a body overwritten right
  after the PATCH, be reported (and promoted) as published; the body comparison is what makes
  "published" mean "the notes are what CMRU intended". Anything else (a different ID, tag or
  body, a read-back that is missing, malformed, an HTTP error or unreachable) is reported
  as `was written ...` or `WAS WRITTEN, but reading it back ... failed`, with the release
  left in place and the instruction to inspect the release and the tag before any retry.
  The read-back is by release ID (a create reads the ID its response returned; an update the
  captured ID), because the by-tag read can see a different release than the one just written
  and, right after a create, may not see it yet. A create therefore retries the ID read a
  finite number of times (`READBACK_ATTEMPTS`, pauses injected in tests, no unbounded wait) and
  reports "not yet visible" separately from "the tag now belongs to another release"; an update
  reads an already-visible release once. A full-SHA `target_commitish` must equal the release
  commit after both a create and a PATCH; a branch name is not a commit claim. **Anything that
  happens after a POST or PATCH was sent is treated as possibly applied.** An HTTP error, a
  transport failure, a malformed success body and a malformed record are not refusals and
  do not invite a blind retry, because GitHub may have applied the write and then failed to
  answer. The handler never resends: it reads the release back (by tag when a create returned no
  usable ID), returns success only when the exact ID, tag, body and target are verified, and
  otherwise says the outcome is unknown and not rolled back and tells the operator to inspect
  the release and the tag before any retry. No normalization is applied to the body (it must
  match byte for byte); whether real GitHub preserves every body byte is a live check, not
  an assumption. A GitHub error body is not proof of non-application either, so none of these
  claim the run changed nothing. Rolling it back was rejected: deleting or reverting a Release the tag move may
  already have made visible is a second, equally racy write that would also hide what the
  operator needs to see. So the workflow narrows the window and reports honestly; it does
  not claim to prevent every partial public publication.
- **Validated before any side effect, by one function.** `oci-image-release` is the last
  step of a release, after the image is already public, so a configuration mistake found
  there is found too late. `cli._validated_oci_release_image` therefore checks the
  configured command before any `prepare` step, gate, tag or image push, for every project
  the run selects (not project by project, so a later project's mistake cannot surface
  after an earlier one was published). It is the first thing `_release_child` does, ahead of
  a dry run's `external:`-version `prepare` step, which executes project code that may write
  files: checking it only after that step, as an earlier version did, let a malformed handler
  be found after a side effect. The check is pure configuration, so it needs no plan and
  also covers a selected project that turns out unchanged. `cmru release --dry-run` calls
  the same function.
  It refuses a malformed, duplicate, missing or unexpected `--prefix`/`--image`/`--cwd`; a
  prefix that is not the project's release prefix without `-v`; an `--image` with a tag or
  digest; an untagged project (no protected identity can exist); a push step whose
  `bake_set_*`/`no_cache_env` would make the runner append arguments to the command; and a
  `--cwd` that, resolved from the command's own declared `cwd`, is not the project root
  that holds the changelog. The root is derived from CMRU's declared config and the
  candidate worktree (`project.cwd` under the repository root, the same derivation
  `run_project_step` uses), never from a path in the configuration: an absolute `--cwd` or
  command `cwd` is refused because it would bake one candidate's location into a file
  every candidate shares. Anything that cannot be derived is refused, not guessed.
- **Last command of the push step.** The command publishes externally visible metadata
  that announces an image, so it must follow the push of that image, and nothing may follow
  it: a later command's failure would leave a public release for a run CMRU reports as
  failed. CMRU cannot tell which earlier command pushed the image, so the general contract
  is positional and mechanical: it must be the final command, and a command after it is
  refused before any side effect.
- **One body owner per push step.** `wheel-publish`, `tarball-publish` and
  `oci-image-release` each write the whole body, so the later one would silently replace the
  earlier. A push step combining `oci-image-release` with another body handler (or a second
  one) is refused before the release starts, rather than letting one body win by order.
- **"Published" means the handler is there.** The notes-published claim in logs and dry runs
  is made only when the configured `push` step contains a CMRU body handler. An image push
  without `oci-image-release` still carries the notes in the tagged changelog and says so,
  and `--no-build` previews the changelog section instead.

Image-only publishers that do not use a CMRU body handler (for example a bare registry push)
have no GitHub Release body; their notes stay in the changelog.

## Release history errors refuse the plan

CMRU uses `git log` to decide whether a registered project changed since its release tag.
An empty successful result means there are no matching commits; a nonzero result means CMRU
could not determine that fact. Returning an empty list on Git failure would silently skip the
project, so release planning preserves Git's diagnostic and refuses before any project cycle
starts. The `cmru status` and release paths share this fail-closed history reader.

### Local tag inspection requires Git 2.43

Release and cleanup workflows distinguish an absent local tag from a failed ref lookup. The
hash-returning `git show-ref --verify` form does not give those cases distinct exit statuses, so
CMRU first asks `git show-ref --exists` (exit 2 means absent; exit 1 means lookup error), then
resolves the exact object ID. `--exists` was added in Git 2.43. On an older Git, CMRU refuses
local tag inspection with an explicit version requirement instead of treating an ambiguous
lookup as absence. For a real release that includes tagged projects, the launcher checks support
before creating the candidate worktree, running a project gate, or creating a tag. When selected
projects span independent Git families, it fetches each family's origin/main snapshot, reads each
selected project's tag policy from that snapshot, then checks all tagged families before dispatching
any one family. Each launcher receives the checked commit, fetches `origin/main` again under its
release lock, and refuses before creating a candidate if the ref moved after preflight. The
handoff travels through a private inherited pipe, and the child's fresh fetch confirms that the
commit is authoritative instead of trusting the descriptor alone. For an in-repository
orchestration config, preflight also reads the selected project's config path from that snapshot
instead of trusting a caller checkout that may point to an older path. Project config symlinks are
resolved from the selected repository path in the same Git tree, and a target outside the family
is refused. This preserves the link path when the caller's checkout still points to an older
target. The resolved target must remain named `cmru.toml`, matching the config loader's filename
rule. Project secret overlays are never copied into the snapshot: the launcher resolves them from
the caller's config area and hands the tokens to the child (see "Keeping release credentials
out of gate containers"). Resume reads the config path and tag policy from the committed retained candidate before
starting its child.
The selected top-level config link can use an alias basename; snapshot and child resolution
classify it by the resolved target's canonical config filename. When the checkout is reached
through a repository symlink, CMRU maps the ancestor to the Git root while preserving link
components inside the tree.
Cleanup checks during its preview, before the captured plan can be applied. These checks keep a
repository read failure from silently skipping or postponing a tag operation.

## Known project scopes localize version bumps

CMRU discovers changed projects from paths, then derives each project's Conventional Commit
bump from subjects in its release range. A scope matching a configured project name or alias
applies that subject's feature or breaking effect only to the matching project. This lets one
reviewed integration commit say `feat(assay)!: change verdict format` without turning every
touched project's version into a major release. An unscoped subject retains its historical
effect on every changed project. An unknown scope also retains path-based behavior: treating an
unrecognized value as narrow could turn a typo into a silent downgrade. Inspect the result with
`cmru status`, or use `cmru release <project> --set-version <version>` for an explicit version.
The scope registry must be identical when CMRU computes the status bump, release tag, generated
changelog heading, and published notes. A selected-project-only registry can mistake another
configured scope for an unknown one and give the release metadata a different version from the
tag.

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
remote state retains the tag and candidate for inspection. A same-name remote tag with a
different ref object is a conflict, not proof of absence, so CMRU preserves the local ref and
candidate. After a confirmed-absent attempt, the same tag name may acquire a new annotated-tag
object on retry; the ledger permits that rotation only when the prior exact object has a matching
origin-absence proof. CMRU promotes `origin/main` from the same candidate only after
publication succeeds. This keeps a failed build or upload out of `main` and lets a later project
consume an earlier project's completed release in the same run. Resume checks attempted release
tags and recorded results against origin before replaying a candidate. It refuses when a tag may
have been pushed without a completed result or when a recorded result has not been promoted; it
also keeps an existing candidate after a release-plan refusal. This prevents a successful child
exit from erasing evidence of an incomplete post-tag release.

The promotion is a push of the exact candidate. CMRU does not rebase the candidate
when another writer advances `origin/main`, because that would change the SHA that was gated and
used to build the artifact; it merges `origin/main` into the candidate (bounded, `--no-ff`) only
when the new commits leave the project's own paths untouched, otherwise it stops (REL-04). The candidate branch and worktree remain available for inspection;
success deletes the now-redundant branch. A version strategy that creates a mechanical version
commit receives a second gate on that exact commit before publication.

### Release from an origin-backed feature worktree (KI-82)

The default release source remains `origin/main`. `cmru release --from-worktree` is the
accepted extension for operators whose integration branch cannot yet be reconciled. It requires
a clean, named non-main worktree branch and the same-named `origin/<branch>` at the exact same
fetched commit. It refuses local-only or remote-only commits, detached HEAD, dirty paths, and
selections spanning more than one Git family. The branch may diverge from local `main`; this
mode does not fetch or compare with `origin/main`, push to it, move local `main`, or synchronize
local `main`.

CMRU's generated transaction branch remains separate from the caller's feature branch. Its
same-named origin candidate is the durable PR branch. CMRU checks that this remote name is free
before creating the transaction and uses expected-old-value push leases on later candidate
updates. If the candidate no longer fast-forwards, CMRU stops and retains it for inspection; it
does not rebase or force-update the branch. The shared workspace record marks feature-source mode
as well as the source identity sidecar. The child checks both at startup and before candidate
pushes, so a missing sidecar cannot fall back to default main promotion. It never pushes the
caller's feature branch. Resume takes the source mode from the transaction record, not from the
caller's flags: a resumed feature candidate re-applies `--from-worktree` automatically, so it can
never be replayed as a default main promotion, and `--from-worktree --resume` naming a default
candidate is refused for the same reason. A candidate that already published every selected
project is refused on resume because no safe side effect remains to replay; its next step is the
PR merge and `cmru abandon`. After publication, it retains the
local candidate worktree and both candidate refs. A PR may be open or blocked while the release is
already public; this is accepted because the artifact and tag bind to the gated candidate commit
and do not wait for `main` integration. Keep that candidate and its evidence until integration is
deliberate. A later fast-forward or merge commit preserves the tagged SHA in `main`'s ancestry;
squash and rebase do not. `cmru abandon <candidate-branch>` can retire a completed feature
candidate only after its exact final commit is present in `origin/main`; it removes the candidate
refs and local transaction metadata while preserving the public release tags and assets. If a
hosting service has already deleted the PR head after merge, retirement can still remove the local
transaction after CMRU proves the exact final candidate commit is in fetched `origin/main` and
rechecks the release tags. An absent branch by itself is not proof of a merge. Because
`abandon` has no `--secret-file` (only `release` and `publish` accept it), run retirement from a
checkout with origin credentials, usually the main checkout.

Credential lookup follows the same separation. A linked worktree may not contain the ignored
root `cmru.secret.toml` stored in the main checkout. CMRU can report a safely inferred path and
an explicit way to select it, but must not silently consume another checkout's secret. The
`--secret-file PATH` option of `release` (and of `publish`, for a tagged OCI release-body retry)
authorizes the parent process to read that root secret;
environment-token precedence and project secret overlays still apply. The existing private
handoff keeps the resolved credential out of the isolated transaction worktree, child argv,
environment, and logs. This behavior is implemented in the current candidate; stable
availability waits for KI-82's registered gate and acceptance evidence.

## Release uses a wheel-bound controller

`cmru release` re-executes the controller inside its isolated candidate worktree. If the caller
starts from an editable checkout, a later process could import different CMRU bytes from the ones
that began the transaction. A real release therefore requires CMRU's active CLI module to match
its installed distribution, requires non-editable wheel provenance, and hashes the installed
distribution files before starting the transaction and again inside the child. Both logs record
the controller version, original wheel digest, and installed-file digest. This keeps the release
mechanism itself reviewable and identifies the exact controller if a transaction must be resumed.
`cmru release --dry-run` remains available from a source checkout because it cannot publish or
promote anything.
