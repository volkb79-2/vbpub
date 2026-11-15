---
name: ciu-stack
description: Ad-hoc parallel or verification ciu stack lifecycle — Mode-B worktree bring-up, CPU-governance capping (CIU-90), host-load check before spinning up, teardown. Use when you need a throwaway or parallel stack instance beyond a project's primary Mode-A shared one — e.g. post-merge verification running alongside another gate, or a time-boxed multi-stack authorization. Applies to any ciu-managed project.
---

> **Verify against the installed tool:** `ciu help worktree` / `ciu worktree <verb> --help`
> is the generated, authoritative grammar (options are exact long spellings; no `-y`).
> Checked against **ciu 7.25.1**; `ciu skills check` reports when this skill is stale for
> the installed ciu. run-gate RG-39 (run-gate 23.5.0) serializes two consumers of the
> SAME container automatically; the `flock` convention below stays valid as a cheap
> outer/pre-emptive lock, just no longer load-bearing for correctness. If
> `governance.cpus` moves, re-verify the CPU section against CIU's own CHANGES.md before
> trusting it verbatim.

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
ciu worktree create <name> --profile core,db --base main   # creation does NOT deploy; add --up to start it
cd <repo-root>/.worktrees/<name> && eval "$(ciu env print)"   # exports THIS instance's own REPO_NAME/INSTANCE_ID
ciu render
ciu up --deploy --healthcheck        # full stack — --healthcheck is required, not optional

# For a single sub-stack (e.g. just test-runner) instead of the full stack:
ciu up --dir tools/test-runner --deploy --healthcheck   # --healthcheck gates that one stack after it starts
```

`create` records the fork point (`fork_point_sha`, CIU-106) and `base_ref` in
`ciu.worktree-instance.json`, and run-gate reads that record to derive a comparison
base when a lane delegates its base. The record is ONE contract with the live Git
state: inspect it with `ciu worktree inspect NAME [--json]` and never move, rename or
hand-edit a managed checkout to make them agree — CIU refuses a record/Git mismatch
instead of repairing it silently.

The other lifecycle verbs, so you do not hand-roll them:

```bash
ciu worktree ensure NAME [--profile P1,P2] [--adopt-existing-network]  # idempotent: reuse, create, or resume a partial
ciu worktree adopt NAME PATH [--profile P1,P2]                         # take over an unmanaged linked checkout
ciu worktree up NAME [--all]                                           # start a ready instance (never implicit)
ciu worktree lease NAME [--extend 24h | --perpetual | --release]       # explicit ownership, no Docker state read
ciu worktree refork NAME                                              # re-record the fork point after a rebase onto trunk
ciu worktree list [--json]   ciu worktree inspect NAME [--json]
ciu worktree branches [--yes]                                         # prune exactly the merged, clean, unprotected branches
ciu worktree reap [--yes]                                             # destroy only record/lease/label-proven groups
ciu worktree exec NAME [--target ALIAS] -- argv ...          # exact argv, no shell; starts nothing implicitly
```

`ensure` is the automation spelling (it never rewrites a conflicting instance; on an
existing record omit the initial-overlay options). `reap` and `branches` are surveys
until `--yes`; run them without it first.

## CPU governance (CIU-90), when a host is shared and a throwaway instance needs capping

- **Default: a fresh Mode-B `test-runner` comes up UNCAPPED** (`NanoCPUs=0`)
  — the tracked `tools/test-runner/ciu.defaults.toml.j2` sets no `cpus` key.
  A project's own shared Mode-A instance may cap itself some OTHER way —
  don't assume it comes from this same render path without checking.
- **Two conditions, both needed**: `[<stack>.governance].enabled = true` (the
  overlay is gated on `enabled`; with it false the `cpus` value is parsed and
  never emitted) and `cpus = "<N>"` (a decimal string, S15.21 — the Compose
  `cpus` key, e.g. `"1.5"`; empty means no limit). Every memory/CPU control
  defaults to unset, so an upgrade never silently throttles a currently
  uncapped container.
- **Where**: in **THAT INSTANCE'S OWN render-input copy** of the toml — never the
  tracked template (that would silently change the default for every future
  instance — a shadowing-default hazard). Re-render, re-up. A stack's table
  overrides matching keys from the global `[governance]` and only the keys it
  restates; a value authored directly on a Compose service still wins.
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

`run-gate` names a lane's execution environment, so a worktree lane still runs in
`tester-unified` (never in this cockpit); see the **run-gate-cli** skill for
`--worktree`/`--base`/`--jobs`/`--shard` mechanics and for why the base comes from
this worktree's own `ciu.worktree-instance.json` record.

## Teardown

`ciu worktree rm <name> --yes` from main (runs `ciu clean`, then removes the
checkout). Check `git status --short` first if the instance might hold
deliberate scratch edits worth keeping.

`rm` keeps an unmerged branch tip by default and needs EXACT-BRANCH consent for
anything destructive: `--discard-changes BRANCH` for tracked/untracked changes,
`--discard-protected-state BRANCH` for the protected ignored state (`.assay/`,
`vol-*`), `--delete-branch` for an unmerged branch, `--root-escalation BRANCH`
after a confirmed permission failure, `--keep-branch` to keep a merged one, and
`--dry-run` to list what it would do. A bare `--force` is not the consent flag.

For a Mode-B stack, teardown must resolve BOTH volume prefixes
(`<branch>-<instance>-*` AND bare `<branch>-*`) — verify neither is left behind
(`docker volume ls | grep <name>`).

## KSM and host placement

An ephemeral lane or container gets CIU's KSM shim only when asked: `--ksm`
injects it for one run, `--no-ksm` opts out, and `--no-ksm` is PASSTHROUGH (it never
disables a shim an image enables itself, S15.18). Per AGENTS.md, KSM sharing is not
lane overcommit capacity — never count it.
