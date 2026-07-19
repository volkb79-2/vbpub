---
schema_version: 1
id: nyxloom-P44-role-scoped-dispatch
project: nyxloom
title: "Role-scoped build_dispatch (stop leaking IMPLEMENTER-flavored prompt text to CARVER/FRONTIER_REVIEW)"
tier: sonnet5-high
input_revision: "780b522"
depends_on: []
session: fresh
source: {kind: product-goal, ref: nyxloom-trove/4-backlog.md}
scope:
  touch:
    - "src/nyxloom/adapters.py"
    - "src/nyxloom/daemon.py"
    - "tests/test_adapters.py"
  forbid:
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/types.py"
    - "routes.host.toml"
oracles:
  - id: O1
    observable: "`adapters.build_dispatch` becomes role-aware (e.g. a `role: Role` keyword-only param). IMPLEMENTER keeps TODAY'S prompt text byte-for-byte (the 'Handoff:/Worktree:/Branch:/Gate:/Receipt:' block + the git-add/commit instruction) — zero behavior change for the implementer leg. CARVER gets its own prompt text that never claims 'Branch: {branch}' + 'you MUST git commit' when the carver's own `carve_authority` is 'files' (files authority writes without committing — see daemon.py module docstring above `_CARVE_AUTHORITIES`); when authority is 'branch' or 'main', the carve leg's commit instruction is fine to keep. FRONTIER_REVIEW gets its own prompt text that does NOT tell a reviewer to commit to `cfg.default_branch` (today's bug: daemon.py:2362-2366 dispatches the reviewer with `branch=cfg.default_branch` and the generic prompt's 'you MUST git commit ALL your work on the branch' — telling a reviewer to commit to main). Non-hollow anchors REQUIRED in tests/test_adapters.py: (a) a test asserting `build_dispatch(..., role=Role.IMPLEMENTER)`'s prompt is unchanged from the pre-existing IMPLEMENTER assertions (regression pin); (b) a test asserting a CARVER dispatch under files-authority does NOT contain the commit instruction string; (c) a test asserting a FRONTIER_REVIEW dispatch's prompt does NOT contain 'git commit' or claim a branch to commit to."
    negative: "build_dispatch has no role parameter at all today — every leg (implementer, carver, reviewer) gets the identical implementer-flavored prompt regardless of role or carve_authority. A hollow fix that adds a `role` param but leaves the prompt text identical across all three branches (i.e. the param exists but changes nothing) fails this oracle — the anchors in (b)/(c) must actually observe different prompt content, not just a role label threaded through unused."
    gate: tester-unified
  - id: O2
    observable: "All three existing `adapters.build_dispatch(...)` call sites in daemon.py (the CARVER leg in `_execute_carve_dispatch` ~L1725, the IMPLEMENTER leg ~L2026, the FRONTIER_REVIEW wave-launch leg ~L2362) are updated to pass their own role explicitly (e.g. `role=Role.CARVER` / `role=Role.IMPLEMENTER` / `role=Role.FRONTIER_REVIEW`), and the CARVER call site also passes through its already-in-scope `authority` local variable so build_dispatch can make the files-vs-branch/main commit-instruction distinction from O1. Grep-provable: `grep -c 'role=Role\\.' src/nyxloom/daemon.py` finds all three new keyword arguments at the three call sites above."
    negative: "A call site is updated to just import `Role` without actually passing `role=` to build_dispatch (i.e. the plumbing exists but a call site silently keeps the default), so that leg keeps receiving the wrong-role prompt text — the exact silent-mismatch this package exists to close."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "closing O1's FRONTIER_REVIEW branch cleanly requires understanding review-packet-building code beyond adapters.py/daemon.py's three call sites (e.g. changing what the review packet itself contains) — then keep the FRONTIER_REVIEW prompt change to ONLY the generic wrapper text (drop the misleading branch/commit instruction), do not expand scope to touch how the packet is built."
  - "the only clean fix requires adding a new field to `RouteDef` in config.py (currently forbidden/out of scope here) — BLOCKED, do not edit config.py; report exactly which field would be needed and why."
---

# P44 — Role-scoped `build_dispatch`

Today `adapters.build_dispatch` is called identically for all three dispatched
roles (CARVER, IMPLEMENTER, FRONTIER_REVIEW) and always builds the SAME
implementer-flavored prompt: `Handoff:/Worktree:/Branch:/Gate:/Receipt:` plus
"You MUST git add and git commit ALL your work on the branch." This is a real,
live bug, not just cosmetic:

- The FRONTIER_REVIEW dispatch (`daemon.py:2362-2366`) passes
  `branch=cfg.default_branch` and gets the generic "you MUST git commit ALL
  your work on the branch" instruction — literally telling a **reviewer** to
  commit to **main**.
- The CARVER dispatch (`daemon.py:1725-1728`) gets the same unconditional
  commit instruction even when `cfg.policy.carve_authority == 'files'`, a mode
  whose whole contract (see the module docstring above `_CARVE_AUTHORITIES` in
  daemon.py) is "writes new handoff files WITHOUT committing (no git)."

This is `#34`/backlog item `B7` ("role-scoped build_dispatch (unblocks B6;
fixes all-legs prompt leak)"), and a hard prerequisite for P45 (this same
session's next package): P45 wires a new automatic path that dispatches the
carver from `reconcile.py`'s reject-triage logic, and it must not inherit this
same cross-role prompt leak into a new call site.

## Worktree / branch

Create a git worktree for branch `feat/nyxloom-P44-role-scoped-dispatch` from
local `main` at `/workspaces/vbpub/nyxloom/.worktrees/nyxloom-P44-role-scoped-dispatch`
and do all work there — never modify the main `/workspaces/vbpub/nyxloom`
checkout directly:

```
git worktree add -b feat/nyxloom-P44-role-scoped-dispatch \
  .worktrees/nyxloom-P44-role-scoped-dispatch main
```

Commit all work on that branch.

## Context to read first (read ONLY these)

- `src/nyxloom/adapters.py` L156-238 — `build_dispatch` itself: the prompt
  construction, the `prompt_hints` branches (`incremental-write`,
  `free-endpoint` — leave these alone, they are role-agnostic and correct as
  is), and the per-CLI argv branches (`claude`/`codex`/`opencode`/`reasonix`/
  `fake` — leave these alone too, only the PROMPT TEXT construction changes).
- `src/nyxloom/daemon.py` L1656-1741 (`_execute_carve_dispatch`, in particular
  L1676 `authority = getattr(cfg.policy, "carve_authority", "branch")` and
  L1725-1728 the CARVER call site).
- `src/nyxloom/daemon.py` L2010-2029 (the IMPLEMENTER call site, ~L2026).
- `src/nyxloom/daemon.py` L2345-2366 (the FRONTIER_REVIEW wave-launch call
  site, ~L2362).
- `src/nyxloom/types.py` L132-137 — the `Role` enum (read-only; this package
  does not change it).
- `tests/test_adapters.py` — the existing test file this package extends
  (read its current IMPLEMENTER-path assertions before changing anything, so
  O1(a)'s regression pin is a real pin, not a rewrite).

## Work

1. Give `build_dispatch` a way to know which role it is dispatching (a
   `role: Role` keyword-only parameter is the natural shape; you may also add
   an optional `carve_authority: str | None = None` if that is the cleanest
   way to satisfy O1's files-vs-branch/main distinction for CARVER — your
   call on exact signature, the oracles are behavioral, not prescriptive).
2. Branch the prompt-construction logic by role: IMPLEMENTER keeps exactly
   today's text. CARVER and FRONTIER_REVIEW each get their own short,
   role-correct prompt (they still need to reference the packet path /
   worktree / gate / receipt so the CLI can find its work — just drop or
   rephrase the instructions that are actively wrong for that role, per O1).
3. Update the three call sites in daemon.py to pass their role (and, for the
   CARVER site, its `authority` value if your signature needs it).
4. Add the non-hollow tests from O1/O2 to `tests/test_adapters.py`.

## Gate

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd /workspaces/vbpub/nyxloom/.worktrees/nyxloom-P44-role-scoped-dispatch && \
  PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -p no:cacheprovider 2>&1 | tail -80'
```

Never run this in the devcontainer/cockpit venv — only `tester-unified` counts.

## LOG/REPORT

Write `nyxloom-trove/reports/P44-LOG.md` during implementation (actions,
decisions, blockers) and `nyxloom-trove/reports/P44-REPORT.md` after (gate
output, commit hash, what each oracle's non-hollow anchor actually asserts).
