# AGENTS.md — vbpub agent instructions (cross-tool)

Read by every agent CLI (codex, opencode, Claude Code, and — via its prompt —
Reasonix). Repo-wide rules; each project adds its own specifics under its
`nyxloom-trove/` (see below).

## Writing a handoff / dispatch prompt — honor AUTHORING.md
When you are asked to **start an agent for a task**, or to **write a prompt or a
handoff package**, first read and follow
**`nyxloom/nyxloom-trove/AUTHORING.md`** (the handoff-authoring guide). A handoff
is only as good as its contract: a strong detailed contract, an explicit
"Context to read first" (name the exact files/sections — the token lever),
oracles that assert the *behavioral* contract (not hollow tests), a real gate,
and a **mechanical BLOCKED escape hatch** (escalation is trigger-based, not
"reflect on your expertise"). Product calls become `D-<NNN>` decisions, not
BLOCKED. The guide's frontmatter section makes the handoff nyxloom-compatible
(schema-validated by `nyxloom lint`).

## The gate is never the devcontainer (cockpit doctrine)
The devcontainer is a **cockpit** (inspect + drive). The gating suite runs in a
dedicated container, never here. For the vbpub family that is
**`tester-unified`** (see `tester-unified/`); it must give the run-uid a full
identity (passwd+group+HOME+XDG). "Green in the devcontainer venv" is not a ship
signal.

## Worktree protocol
Parallel implementation runs in `.worktrees/<branch>` (branch from `main`).
Merge serially onto `main` with `--no-ff`; expect minor overlap reconciliation.
Keep packages small + non-overlapping to parallelize. Each worktree has its own
index, so `git add`/`commit` there is private and safe.

## Committing from the shared `main` checkout
The main checkout (`/workspaces/vbpub`) is shared: another agent's serial merge
may `git add`/commit at any moment, so its index is not yours to trust. A plain
`git add <paths> && git commit` can capture whatever a concurrent `git add`
staged — observed live: a wings commit that swept in another agent's `cmru/`
files under the wrong message. When committing from here (not from an isolated
worktree), **scope to explicit paths and bypass the shared index**:

    git commit --only -F msg.txt -- <your paths>     # commits only these paths

then verify: `git show --stat HEAD --name-only | sed 's#/.*##' | sort -u` lists
only your dirs. Do **not** `reset`/`rebase`/`--amend` to repair a contaminated
commit — HEAD may have already moved under concurrent commits; leave the bad one
buried and land a correct new commit instead. (Inside a private worktree none of
this applies — commit normally.)

## Carving for a project — where the specifics live
Project-specific constraints a carve/review agent must honor (schema policy,
gate command, stack/mutex rules, product invariants) live in that project's
`nyxloom-trove/nyxloom.toml` (`[gates.*]`, `[refs]`) and, when distilled, that
project's own `AGENTS.md`. Read the project's `nyxloom-trove/STANDARD.md` (the
layout spec) and its `[refs]` docs before carving for it. Do NOT rely on the
historical `legacy-workflow-origin/` docs — their live rules are already in
nyxloom (schema/lint/review) and this file.
