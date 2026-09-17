---
name: cmru-cli
description: The cmru (Configurable Multi Release Utility) CLI — status/release/build/publish/cleanup, the isolated-worktree release transaction, tester-gate. Use for release/versioning work in any CIU-family monorepo. cmru is the canonical release tool for the estate even before a project has cut its first release.
---

> **Tool versions as of last verified update (2026-09-17):** cmru
> `5.2.2`/`5.2.3.dev` series (`cmru --help` prints the exact dev build in its
> banner; there is no `--version` verb — use the banner or `pip show cmru`).
> Re-verify against `cmru --help` if a flag below drifts.

> **MANDATE.** cmru is the canonical release tool for CIU-family monorepos —
> use it even for a project that hasn't cut a release yet, rather than
> hand-rolling a tag/build/publish sequence with raw `git tag` + `docker
> push`. Never hand-tag a release commit or hand-push a release image; those
> steps exist inside `cmru release`'s isolated transaction specifically so a
> failed step doesn't leave a half-published state.

# cmru CLI

## Typical workflow (run from a project or repo root)

```bash
cmru status                        # preview what changed + the next version (no writes)
cmru release                       # isolated: prepare → gate → integrate → tag → build → publish
cmru cleanup [--project P] [--dry-run]           # prune old releases/images (keeps -latest)
cmru cleanup --remove-assets 30d                  # age-based prune
```

`cmru release` runs the ENTIRE release as one isolated, source-first
transaction in its own worktree; only a FAILED build keeps that worktree
around (for inspection), a successful one retains local non-release
outputs. `cmru build` alone (without `release`) is available for a plain
isolated local build that also retains outputs on success.

## Planning verbs (read-only, no writes)

```bash
cmru status [--config C] [--project P] [--minor|--major] [--set-version V] [--dry-run]
cmru worktrees [--json]                     # discover retained worktrees
cmru dependencies [--config C] [--json] [--write]   # dependency graph + preflight
```

## Release / history (writes)

```bash
cmru release [--config C] [--project P] [--minor|--major|--set-version V] [--dry-run]
             [--no-build] [--resume WORKTREE|--abandon WORKTREE|all-previous]
             [--allow-uncommitted] [--ref REF]
cmru changelog --config C --project P --backfill-tag TAG   # catalog an already-published tagged release
cmru build    [--config C] [--project P]      # isolated local build; retains outputs on success
cmru publish  [--config C] [--project P]      # run the project's 'push' step
```

`--resume WORKTREE` / `--abandon WORKTREE` / `--resume all-previous` recover
a release transaction after an interruption — check `cmru worktrees --json`
first to see what's actually retained before choosing which to resume or
abandon.

## Gating inside a release (tester-unified)

```bash
cmru tester-gate --cwd DIR --image IMG [--cgroup-parent SLICE] [--memory M]
                 [--memory-swap MS] [--cpus N] [--cgroup-probe-image IMG]
                 [--enable-docker]
```
Runs one command inside `tester-unified` for a worktree with declared
resource caps + a host-verified cgroup slice — this is cmru's own
equivalent of dstdns's `run-gate`, for repos in the vbpub/cmru family that
gate via `tester-unified` rather than a per-project `run-gate.toml`.

## Maintenance / discovery

```bash
cmru standards [--config C] [--project P ...] [--update]     # check/update CMRU framework markers
cmru tool-deps [--config C] [--project P ...] [--json]        # verify declared tool deps: integrity +
                                                               # authenticity + freshness (network;
                                                               # NEVER run during tests)
cmru resolve [--config C] [--project P|--prefix PREFIX] [--format env|json|url]
cmru get|get-py --config C --project P [--output FILE]        # emit a standalone get.py installer
cmru init [--layout single|monorepo] [--project ID] [--owner O] [--repo R]
```

`cmru init` never overwrites an existing `cmru.toml`/`cmru.orchestration.toml`
— it validates with the real loaders before writing, so it's safe to run
against a partially-set-up repo to see what it would add.

## Never do instead

Never hand-edit `cmru.toml`'s version/changelog fields to fake a release
state — `cmru status`/`cmru changelog --backfill-tag` are the only correct
ways to reconcile cmru's view of history with what actually got tagged and
published.
