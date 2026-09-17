---
name: ciu-cli
description: Core ciu (Container Infrastructure Utility) verbs beyond worktree lifecycle — env, render, up/down/clean, health, diagnose, status, bake, provenance. Use whenever you'd otherwise reach for docker/docker compose/buildx directly in a ciu-managed repo. See the separate ciu-stack skill for the worktree/multi-instance lifecycle.
---

> **Tool versions as of last verified update (2026-09-17):** ciu `7.13.2`
> (`ciu --help` for the authoritative verb list; this build has no
> `--version` flag — `ciu` with no args prints the version in its banner).
> Re-verify against `ciu --help` / `ciu <verb> --help` if a flag below
> errors; ciu's own help text is generous and current — this skill exists so
> an agent doesn't have to spend a round-trip discovering it, not to replace
> it as the source of truth.

> **MANDATE.** ciu is THE orchestration interface for any ciu-managed repo
> (dstdns AGENTS.md §4.4). Never use these manual alternatives:
> - **Never** `docker compose up --build` / `docker compose build` / a bare
>   `docker build` — use `docker buildx bake <target> --load` then `ciu up
>   --deploy --healthcheck`. `--healthcheck` is not optional: a bare `ciu up`
>   skips ciu's own inter-phase health gate (CIU-68), the only thing making a
>   `stack:*:healthy` provisioning-preflight ref reliable across a phase
>   boundary.
> - **Never** hand-`docker compose down`/`rm -f`/`docker volume rm` a
>   ciu-managed stack — use `ciu down`/`ciu clean` (with `--clean-only-*`
>   flags for a narrower cleanup) so ciu's own bookkeeping (leases, labels,
>   the worktree registry) stays consistent with what's actually running.
> - **Never** hardcode a container/network/project name (`myrepo-dev-postgres`)
>   — read it from `ciu env` / `ciu env print`, or `scripts/config_helper.py`
>   in a consuming repo. A hardcoded name breaks the moment `INSTANCE_ID`
>   changes (a new worktree, a rebuilt devcontainer).
> - **Never** grep `docker ps`/`docker inspect` output to guess whether a
>   stack is healthy — use `ciu status [--profile NAME] [--json]` or
>   `ciu diagnose` (explains common failures, read-only).

# Core ciu verbs

## Environment identity — read it, never hardcode it

```bash
ciu env                       # human-readable KEY=VALUE dump (read-only)
ciu env generate              # (re)generate ciu.env + ciu.instance.generated.toml from system state
eval "$(ciu env print)"       # the shell-integration form: export KEY='value' lines
```

`ciu env print` is the one to `eval` in scripts/gate invocations — it prints
`export` statements safe for `eval "$(...)"` and cannot itself mutate your
shell. It exports `REPO_ROOT`, `PHYSICAL_REPO_ROOT`, `REPO_NAME`,
`INSTANCE_ID`, `DOCKER_NETWORK_INTERNAL`, and more — this is the ONLY
correct source for a container/network name; a value that has an
authoritative source here and gets hardcoded anyway is exactly the
"shadowing default" hazard (dstdns AGENTS.md §4.2a). `ciu.env` itself is a
legacy write-only export as of ciu 7.7.0 — a `source ciu.env` still works
this release (identical key set) but is deprecated; prefer `eval "$(ciu env
print)"` in anything new.

## Stack lifecycle

```bash
docker buildx bake all-services --load     # build-before-deploy (never `--build`/`compose build`)
ciu up --deploy --healthcheck              # deploy full stack + wait for real health
ciu up --profile <name>                    # deploy a named host profile
ciu up --dir <path>                        # bare sub-stack form only — --deploy/--healthcheck are NOT
                                            # valid here (errors "unrecognized arguments")
ciu down [--profile NAME]                  # stop stack, PRESERVE volumes
ciu clean [--clean-only-{config,containers,images,volumes,secrets}]
ciu status [--profile NAME] [--json]       # per-stack compose project, containers, health (read-only)
ciu diagnose [--project NAME] [--logs N] [--json]   # explain common failures (read-only)
ciu health [--profile NAME]                # health gate check
ciu health --preflight [--strict]          # probe images for missing healthcheck tools
```

## Authoring / discovery (no deploy)

```bash
ciu render                    # render ciu.global.toml from the Jinja2 template
ciu profiles                  # list available host profiles
ciu layouts                   # list declared deploy layouts
ciu check [--profile NAME] [--live] [--json]   # validate the config pipeline, no deploy
ciu graph [--format mermaid|dot|json]          # render the dependency graph, no deploy
ciu capabilities [--json]                       # versioned, closed capability allowlist (D-009)
```

## Evidence

```bash
ciu provenance [--ignore-mismatch | --no-preflight] [--json]
```
Verifies RUNNING containers were actually built from the commit under test —
run this before trusting a live/e2e lane's result, so a stale image is
caught as "provenance mismatch" rather than silently judged as if it were
current (dstdns AGENTS.md §4.1a: a stale image is never a blocker, but a
live-test claim that doesn't name its artifact is a defect).

## Dev-loop builds

```bash
ciu bake [targets ...] [--profile NAME] [--no-cache]   # docker buildx bake --load
ciu dev <stack> [--profile NAME]                        # a stack's live dev loop (HMR)
```

## Secrets

```bash
ciu secrets list [-d PATH]
ciu secrets reset [--name N] [-y]
```

## Remote (requires a `.ciu.hosts.toml`)

```bash
ciu host enroll <name> [...]
ciu ssh <host> [--admin] [-- cmd...]
ciu up/down/health --host <name> [selection flags]
```

## Worktree / multi-instance lifecycle

Covered by the separate **ciu-stack** skill (worktree create/ensure/adopt/rm,
governance.cpus capping, flock identity pairing) — reach for that one when
the task is "stand up a parallel or throwaway instance", not this one.
