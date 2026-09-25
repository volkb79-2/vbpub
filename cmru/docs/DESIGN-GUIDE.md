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
same. An exact override requires a reason; when its source timestamp is newer
than the cutoff, it also requires a future expiry date. The reviewable config and artifact diff
remains the control for deliberate holds and urgent fixes.

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
diagnostic emitted by the main parser and the installed `cmru-agent` and
`cmru-controller` dispatchers, including nested verbs. Normal command output is
unchanged.

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

## Why CMRU's release gate declares R0-R3

CMRU's internal `assay.toml` is the authoritative rigor contract for the
selected worktree. R0 runs the existing full test command, R1 judges the
100% line-and-branch coverage artifact against `base..HEAD`, R2 runs Assay's
native serial Python mutation campaign with liveness monitoring, and R3 runs
an import-break canary against CMRU source. `--maxfail=1` stops a failing
mutant at its first failed test; on the known-good baseline it is inert, so
R0/R1 still execute the full suite. Together liveness and fail-fast keep a
mutant's failure from cascading into later tests or leaving a stalled
candidate consuming the lane. The lane uses the estate-approved
`repository-minus-unsafe-symlinks` snapshot and names the three tracked Topos
fixture omissions explicitly; a new unsafe symlink therefore fails closed.

`run-gate.py gate` also retains CMRU's release-specific coverage, mutation,
canary, and real-enrollment lanes. Those lanes provide release evidence and
host-facing checks; they do not replace or silently downgrade the Assay R0-R3
judgment. The selected worktree's Assay source is installed at run time, so
the gate has one reviewed tool source and records its version in the verdict.

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
project is prepared and gated there, then its tag and public artifact are produced from that
fixed commit. CMRU fast-forwards `origin/main` from the same candidate only after publication
succeeds. This keeps a failed build or upload out of `main` and lets a later project consume an
earlier project's completed release in the same run.

The promotion is deliberately a single fast-forward push. CMRU does not rebase the candidate
when another writer advances `origin/main`, because that would change the SHA that was gated and
used to build the artifact. The candidate branch and worktree remain available for inspection;
success deletes the now-redundant branch. A version strategy that creates a mechanical version
commit receives a second gate on that exact commit before publication.
