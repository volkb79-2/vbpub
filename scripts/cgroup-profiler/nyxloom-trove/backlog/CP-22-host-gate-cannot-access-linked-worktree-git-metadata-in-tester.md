---
kind: backlog-entry
schema_version: 1
id: CP-22
title: "Host gate cannot access linked-worktree Git metadata in tester-unified"
status: open
type: "bugfix"
severity: "high"
component: "gate"
context_estimate: "small"
provenance: "2026-10-12 cgroup-profiler tester-unified gate preflight"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler gate"
priority: 1
filed_date: "2026-10-12"
---

## Observed mechanism and reproduction

The registered cgroup-profiler gate passes its judged checkout path to `tools/gate.sh`. That script mounted the host checkout at `/work` in `tester-unified`, while a linked worktree's `.git` file points to an administrative directory under the shared Git common directory at its original absolute cockpit path. The gate container therefore could not resolve Git metadata for a linked worktree. This made the project gate fail before exercising the candidate code.

Reproduce by invoking the registered `unit` lane against a linked worktree whose `.git` file points outside the checkout. The host gate must bind both the checkout and the shared Git common directory into the same absolute paths the checkout expects.

## Why cgroup-profiler owns it

`tools/gate.sh` is cgroup-profiler's declared gate driver and owns the host-to-tester checkout mounts. The failure occurs in that driver before the project suite starts.

## Proposed contract

The host gate maps cockpit-visible checkout paths to Docker host paths, mounts the judged checkout read-only at its original absolute container path, and, for a linked worktree, mounts the shared Git common directory read-only at its original absolute path. Its working directory and `PYTHONPATH` use the checkout's original path. A normal checkout with a `.git` directory continues to use one checkout mount. Error guidance for loading `tester-unified:local` uses the supported `docker buildx build --load` form.

## Oracles

- A command-construction regression runs `tools/gate.sh` with a fake Docker CLI and a real temporary linked Git worktree, then verifies both read-only mounts target the original absolute paths and the test working directory uses the checkout path.
- The registered unit and coverage lanes pass against a linked worktree using `tester-unified`.
- A main-checkout invocation still mounts the checkout and runs successfully without adding a separate Git common-directory mount.
- The missing-image remedy names the supported `docker buildx build --load` command, so a remote BuildKit result is loaded into the local Docker image store used by the gate.

## SPEC ownership

`scripts/cgroup-profiler/tools/gate.sh` owns host path resolution and read-only mounts for the project's tester-unified gate.

## Updates

**2026-10-12** — 2026-10-12 Sol xhigh review fixes: Docker bind source/target fields are CSV-quoted; path mapping reads the raw requested path from the environment rather than awk -v. The regression now uses a checkout path containing a comma and literal backslash-t, supplies nested and non-boundary decoy mounts, checks the exact two mounts for linked worktrees, and verifies the normal checkout gets only its checkout mount.

**2026-10-12** — 2026-10-12 follow-up after review 2: host path mapping now reads Docker inspect's JSON mount list and compares raw paths in Python, preserving backslash and tab characters as well as component boundaries. Bind source/target fields are CSV-quoted, including comma and quote characters. The extra common-directory environment override was removed; the host mapping must derive from the cockpit's actual mounts. Missing-image guidance now only prints the default tester-unified build for the default tag and does not prescribe it for a custom image.

**2026-10-12** — 2026-10-12 follow-up after review 3: host and Git-common paths now cross the shell boundary as NUL-terminated values, so command substitution cannot strip a valid trailing newline. The common-directory refusal now directs operators to expose the actual Git common directory through a cockpit bind mount; it no longer names a removed override.

**2026-10-13** — 2026-10-13 follow-up after review 4: Git's reported common-directory path is also captured with a NUL terminator and exactly one output-record newline is removed. The regression stubs /workspaces/vbpub/.git with a path that itself ends in a newline and confirms the exact Docker target survives.
