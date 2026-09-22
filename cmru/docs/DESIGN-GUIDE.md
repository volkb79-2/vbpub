# CMRU design guide

This document records why CMRU's configuration and command selection work the way they do.
The [README](../README.md) describes the shipped surface; [CONSUMERS.md](CONSUMERS.md)
shows the files an adopter can copy and load.

The closed runtime contract is intentionally small and versioned:

```toml
schema_version = 1

[runtime]
kind = "none" # or "ciu"
```

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

CMRU names a transaction with the shared six-character workspace identity. The
final basename contains that token, so the adapter gives the neutral allocator
an explicit canonical allocation identity path and keeps it in the durable
record; resume and cleanup validate the same structured fact instead of
recomputing a second token from a circular final name.

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
native serial Python mutation campaign, and R3 runs an import-break canary
against CMRU source. The lane uses the estate-approved
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
