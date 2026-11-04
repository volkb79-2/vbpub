---
name: ciu-cli
description: Core ciu (Container Infrastructure Utility) verbs beyond worktree lifecycle — env, render, up/down/clean, status (report, health gate, preflight), diagnose, check, graph, bake, provenance, doctor. Use whenever you'd otherwise reach for docker/docker compose/buildx directly in a ciu-managed repo. See the separate ciu-stack skill for the worktree/multi-instance lifecycle.
---

> `ciu help [verb]` / `ciu <verb> --help` is the authoritative, generated
> grammar (every verb is registered with cli-extended); this skill exists so an
> agent doesn't spend a round-trip discovering it, not to replace it. Options are
> EXACT long spellings: there are no short options (`--yes`, never `-y`; `--dir`,
> never `-d`) and no prefix abbreviations. Machine output is `--json` where a verb
> offers it. `ciu doctor` checks the environment; `ciu skills check` tells you when
> this skill is stale (re-run `ciu skills install` after every ciu upgrade).

> **MANDATE.** ciu is THE orchestration interface for any ciu-managed repo
> (a consuming project's own AGENTS.md may state this explicitly too —
> dstdns's does, at §4.4). Never use these manual alternatives:
> - **Never** `docker compose up --build` / `docker compose build` / a bare
>   `docker build` — use `docker buildx bake <target> --load` then `ciu up
>   --deploy --healthcheck`. `--healthcheck` is not optional: a bare `ciu up`
>   skips ciu's own inter-phase health gate (CIU-68), the only thing making a
>   `stack:*:healthy` provisioning-preflight ref reliable across a phase
>   boundary.
> - **Never** hand-`docker compose down`/`rm -f`/`docker volume rm` a
>   ciu-managed stack — use `ciu down`/`ciu clean` so ciu's own bookkeeping
>   (leases, labels, the worktree registry) stays consistent with what's
>   actually running.
> - **Never** hardcode a container/network/project name (`myrepo-dev-postgres`)
>   — read it from `ciu env print`, or `scripts/config_helper.py`
>   in a consuming repo. A hardcoded name breaks the moment `INSTANCE_ID`
>   changes (a new worktree, a rebuilt devcontainer).
> - **Never** grep `docker ps`/`docker inspect` output to guess whether a
>   stack is healthy — use `ciu status [--profile NAME] [--json]` or
>   `ciu diagnose` (explains common failures, read-only).

# Core ciu verbs

## Environment identity — read it, never hardcode it

```bash
ciu env generate              # (re)generate ciu.env + ciu.instance.generated.toml from system state
eval "$(ciu env print)"       # the shell-integration form: export KEY='value' lines
```

`ciu env print` is the one to `eval` in scripts/gate invocations — it prints
`export` statements safe for `eval "$(...)"` and cannot itself mutate your
shell. It exports `REPO_ROOT`, `PHYSICAL_REPO_ROOT`, `REPO_NAME`,
`INSTANCE_ID`, `DOCKER_NETWORK_INTERNAL`, and more — this is the ONLY
correct source for a container/network name; a value that has an
authoritative source here and gets hardcoded anyway is exactly the
"shadowing default" hazard (a project's own defaults-and-fallbacks policy,
if it has one — dstdns's is AGENTS.md §4.2a). `ciu.env` itself is a
legacy write-only export as of ciu 7.7.0; the bare `ciu env` dump was removed
in 7.20 — use `ciu env print`.

## Stack lifecycle

```bash
docker buildx bake all-services --load     # build-before-deploy (never `--build`/`compose build`)
ciu up --deploy --healthcheck              # deploy full stack + wait for real health
ciu up --profile <name>                    # deploy a named host profile
ciu up --dir <path> [--deploy] [--healthcheck]   # one stack directory (engine mode)
ciu up --dry-run                           # render + validate, do not call Docker
ciu down [--profile NAME]                  # stop stack, PRESERVE volumes
ciu clean --yes                            # remove containers + volumes + rendered artifacts
ciu status [--profile NAME] [--json]       # per-stack compose project, containers, health (read-only)
ciu status --gate [--profile NAME]         # health gate (exit status is the verdict; replaces `ciu health`)
ciu status --preflight [--strict]          # probe images for missing healthcheck tools
ciu diagnose [--project NAME] [--logs N] [--json]   # explain common failures (read-only)
```

A `[deploy] protected = true` instance additionally needs
`--i-understand-this-is-protected` with `--yes` for `down`/`clean` (CIU-105).

## Authoring / discovery (no deploy)

```bash
ciu render                    # render ciu.global.toml from the Jinja2 template
ciu show profiles             # list available host profiles (--json too)
ciu show layouts              # list declared deploy layouts (--json too)
ciu check [--profile NAME] [--live] [--json]   # validate the config pipeline, no deploy
ciu graph [--format mermaid|dot | --json]      # render the dependency graph, no deploy
ciu schema capabilities [--json]               # versioned, closed capability allowlist (D-009)
ciu migrate --check [--json]                   # stale artifacts left by an older ciu
```

## Evidence

```bash
ciu provenance [--ignore-mismatch | --no-preflight] [--json]
```
Verifies RUNNING containers were actually built from the commit under test —
run this before trusting a live/e2e lane's result, so a stale image is
caught as "provenance mismatch" rather than silently judged as if it were
current (a stale image is never itself a blocker, but a live-test claim
that doesn't name its artifact is — dstdns's AGENTS.md §4.1a states this
explicitly, but the principle applies to any project trusting a live
result).

## Dev-loop builds

```bash
ciu bake [targets ...] [--profile NAME] [--no-cache]   # docker buildx bake --load
ciu dev <stack> [--profile NAME]                        # a stack's live dev loop (HMR)
ciu governance ksm [--force]                            # build the KSM shim (S15.17)
```

## Secrets

```bash
ciu secrets list [--dir PATH]
ciu secrets reset [--dir PATH] [--name N] [--yes]
ciu secrets host <host> (--materialize | --list | --path NAME)   # pre-Vault host secrets (S14.3a)
```

## Remote (requires a `.ciu.hosts.toml`)

```bash
ciu host enroll <name> [...]
ciu ssh <host> [--admin] [-- cmd...]
ciu up/down --host <name> [selection flags]
ciu status --gate --host <name> [--thin]
```

## Environment check

```bash
ciu doctor [--check NAME] [--json]     # docker, compose, buildx, git, ssh tools, CIU root, skills
ciu skills check                       # exit 1 unless the installed skills match this ciu
```

## Worktree / multi-instance lifecycle

Covered by the separate **ciu-stack** skill (worktree create/ensure/adopt/rm,
governance.cpus capping, flock identity pairing) — reach for that one when
the task is "stand up a parallel or throwaway instance", not this one.
