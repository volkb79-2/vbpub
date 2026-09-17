---
name: ciu-stack
description: Ad-hoc parallel or verification ciu stack lifecycle — Mode-B worktree bring-up, CPU-governance capping (CIU-90), host-load check before spinning up, teardown. Use when you need a throwaway or parallel stack instance beyond a project's primary Mode-A shared one — e.g. post-merge verification running alongside another gate, or a time-boxed multi-stack authorization. Applies to any ciu-managed project.
---

> **Tool versions as of last verified update (2026-09-03):** ciu `7.11.0`,
> run-gate `23.5.0` (pip-installed; shipped RG-39 — an internal exec-mode
> mutex keyed by the resolved container name, so two consumers of the SAME
> container now serialize automatically without caller coordination; the
> `flock` convention below stays valid as a cheap outer/pre-emptive lock,
> just no longer load-bearing for correctness). If `ciu up --dir <path>
> --deploy --healthcheck` starts working (it currently errors "unrecognized
> arguments" — that flag pair is only valid for a full `ciu up`, not a
> `--dir` sub-stack), or if `governance.cpus` moves, re-verify this file
> against `ciu --help` / CIU's own CHANGES.md before trusting it verbatim.

> **Canonical, repo-agnostic skill.** The worktree lifecycle, uniquification
> (`REPO_NAME`/`INSTANCE_ID`), and `governance.cpus` capping mechanics here
> are universal to any ciu-managed repo — this is what makes them worth
> owning as a ciu skill rather than a per-project doc. A consuming project's
> own vendored copy carries only its host capacity, concurrency-policy
> directive, and concrete trove paths as a short appendix; substitute yours
> if you're reading this outside that copy. **Never a raw `git worktree add`
> or `docker compose up`** where `ciu worktree` / `ciu up` covers the same
> ground — that is the manual alternative this tool exists to replace.

# Ad-hoc parallel stack lifecycle (ciu)

## Before spinning one up

1. **Check host load via PSI, not `free`/`uptime`**: `cat /proc/pressure/{cpu,memory,io}`
   — a real "some avg10" spike means contention; overcommit itself is by
   design on many hosts, not evidence of a problem on its own. Also check for
   another session's own queue (e.g. via `ListAgents` in an agent-orchestration
   context) before blaming yours.
2. **Know the CURRENT concurrency authorization, don't assume a past one
   still holds.** Every project sets its own standing default (agents,
   stacks) in its own operating docs — check the project's own AGENTS.md/
   GUIDE.md-equivalent, and confirm any time-boxed exception is still live
   before running more than the standing default.
3. **Every container competing for the SAME flock needs the SAME identity
   pair to actually serialize** (RG-39): `eval "$(ciu env print)"` exports
   `REPO_NAME`/`INSTANCE_ID`; lock as
   `flock "/tmp/${REPO_NAME}-${INSTANCE_ID}-testrunner.lock" ...`. A
   genuinely separate Mode-B instance gets its own pair, hence real
   parallelism — don't assume isolation without checking `ciu env print`
   inside each worktree.

## Bring-up (Mode-B)

```bash
ciu worktree add <name> --profile core,db --base main
cd <repo-root>/.worktrees/<name> && eval "$(ciu env print)"   # exports THIS instance's own REPO_NAME/INSTANCE_ID
ciu render
ciu up --deploy --healthcheck        # full stack — --healthcheck is required, not optional

# For a single sub-stack (e.g. just test-runner) instead of the full stack:
ciu up --dir tools/test-runner       # bare form ONLY. --deploy/--healthcheck are NOT valid with --dir
                                      # (errors "unrecognized arguments: --deploy --healthcheck")
```

## CPU governance (CIU-90), when a host is shared and a throwaway instance needs capping

- **Default: a fresh Mode-B `test-runner` comes up UNCAPPED** (`NanoCPUs=0`)
  — the tracked `tools/test-runner/ciu.defaults.toml.j2` sets no `cpus` key.
  A project's own shared Mode-A instance may cap itself some OTHER way —
  don't assume it comes from this same render path without checking.
- **To cap an instance**: set `[<stack>.governance].cpus = "<N>"` in **THAT
  INSTANCE'S OWN render-input copy** of the toml — never the tracked
  template (that would silently change the default for every future
  instance — a shadowing-default hazard). Re-render, re-up.
- **Verify it actually applied**:
  `docker inspect --format 'nanocpus={{.HostConfig.NanoCpus}}' <container>`
  — `3000000000` = 3 cores. An empty/zero result means the override didn't
  take; check you edited the render-input copy `ciu render` actually read,
  not a stale path.

## Gate dispatch against it

Same flock convention as bring-up, keyed to THAT instance's own identity pair:

```bash
cd <repo-root>/.worktrees/<name> && eval "$(ciu env print)"
flock "/tmp/${REPO_NAME}-${INSTANCE_ID}-testrunner.lock" \
  run-gate <lane> --worktree <repo-root>/.worktrees/<name>
```

## Teardown

`ciu worktree rm <name> -y` from main (runs `ciu clean`, then removes the
checkout). Check `git status --short` first if the instance might hold
deliberate scratch edits worth keeping. For a Mode-B stack, teardown must
resolve BOTH volume prefixes (`<branch>-<instance>-*` AND bare `<branch>-*`)
— verify neither is left behind (`docker volume ls | grep <name>`).
