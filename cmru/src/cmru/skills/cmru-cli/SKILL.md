---
name: cmru-cli
description: The cmru (Configurable Multi Release Utility) CLI — status/release/build/publish/cleanup, the isolated-worktree release transaction, tester-gate. Use for release/versioning work in any CIU-family monorepo. cmru is the canonical release tool for the estate even before a project has cut its first release.
---

> **Tool versions as of last verified update (2026-09-17):** cmru
> `5.2.2`/`5.2.3.dev` series (`cmru --help` prints the exact dev build in its
> banner; `--version` is not a flag, but `cmru version` IS a real verb —
> use that, the banner, or `pip show cmru`).
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
cmru cleanup --policy [--dry-run]                 # prune per the configured [cleanup] policy (keeps -latest)
cmru cleanup --remove-assets 30d                  # age-based prune (exactly one cleanup mode is required)
```

`cmru release` runs the ENTIRE release as one isolated, source-first
transaction in its own worktree; only a FAILED build keeps that worktree
around (for inspection), a successful one retains local non-release
outputs. `cmru build` alone (without `release`) is available for a plain
isolated local build that also retains outputs on success.

## Planning verbs (read-only, no writes)

```bash
cmru status [P] [--config C] [--minor|--major] [--set-version V] [--dry-run]
cmru worktrees [--json]                     # discover retained worktrees
cmru dependencies [--config C] [--json] [--write]   # dependency graph + preflight
```

## Release / history (writes)

```bash
cmru release [P] [--config C] [--minor|--major|--set-version V] [--dry-run]
             [--no-build] [--resume WORKTREE] [--discard logs|artifacts|evidence]
             [--allow-uncommitted] [--ahead-check-ref REF]
cmru changelog [P] --config C --backfill-tag TAG   # catalog an already-published tagged release
cmru build [P] [--config C]      # isolated local build; retains outputs on success
cmru publish [P] --build-output ID   # publish a retained build's exact bytes
cmru publish [P] --from-checkout     # explicit: run the project's 'push' step from the caller's checkout
cmru abandon [BRANCH|ABSOLUTE-PATH] [--dry-run] [--yes]   # discard a retained release/build transaction
```

`release --resume WORKTREE` recovers a pre-tag release transaction after an
interruption; `cmru abandon` discards one. Check `cmru worktrees --json`
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
cmru standards [P] [--config C] [--update]     # check/update CMRU framework markers
cmru tool-deps [P] [--config C] [--json]        # verify declared tool deps: integrity +
                                                               # authenticity + freshness (network;
                                                               # NEVER run during tests)
cmru resolve [P] [--config C] [--format env|json|url]
cmru get-py [P] --config C [--output FILE | --output-dir DIR]   # emit a standalone get.py installer
cmru init [--root PATH] [--layout single|monorepo] [--owner O] [--repo R] [--owner-type user|org]
cmru version                                                  # print the installed cmru version
```

`cmru init` never overwrites an existing `cmru.toml`/`cmru.orchestration.toml`
— it validates with the real loaders before writing, so it's safe to run
against a partially-set-up repo to see what it would add.

## Never do instead

Never hand-edit `cmru.toml`'s version/changelog fields to fake a release
state — `cmru status`/`cmru changelog --backfill-tag` are the only correct
ways to reconcile cmru's view of history with what actually got tagged and
published.
