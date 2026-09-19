# CMRU design guide

This document records why CMRU's configuration and command selection work the way they do.
The [README](../README.md) describes the shipped surface; [CONSUMERS.md](CONSUMERS.md)
shows the files an adopter can copy and load.

## Context comes from the nearest CMRU root

CMRU searches the current directory and every parent up to the filesystem root for the
nearest `cmru.orchestration.toml`. That file establishes the CMRU root, even when it sits
above several Git repositories. A nearer orchestration file starts a nested CMRU root. An
explicit `--config` path takes precedence over discovery.

The search is filesystem based because a user may deliberately keep shared orchestration
policy outside repository directories. A discovered project `cmru.toml` must be registered
by the selected central file. A nearer unregistered project file is an error instead of being
silently routed through an outer project, and symlinked paths cannot escape the CMRU root.

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

## Candidate-first promotion protects the source history

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
