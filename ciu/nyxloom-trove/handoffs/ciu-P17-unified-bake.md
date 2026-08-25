---
schema_version: 1
id: ciu-P17-unified-bake
project: ciu
component: cli
title: "Public `ciu bake --profile NAME` resolves the selection model (same chain as `ciu up --profile`) and builds only matching targets; remove the dead internal action_build/--build path"
tier: implement-1
input_revision: "370ea8141f7f69399a751f2d5731a8ccf5419921"
source: {kind: backlog, ref: "docs/BACKLOG-2026-08-24.md#CIU-QOL-7"}
stack: none
depends_on: [P16]
session: fresh
scope:
  touch:
    - "src/ciu/cli.py"
    - "src/ciu/deploy.py"
    - "tests/tests/test_ciu_cli_bake.py"
    - "tests/tests/test_ciu_deploy_actions_remaining.py"
    - "tests/tests/test_ciu_deploy_deeper8.py"
    - "docs/SPEC.md"
    - "docs/FEATURES.md"
    - "CHANGES.md"
    - "docs/BACKLOG-2026-08-24.md"
    - "nyxloom-trove/reports/ciu-P17-unified-bake-LOG.md"
  forbid:
    - "src/ciu/engine.py"
    - "src/ciu/deploy_pkg/profiles.py"
    - "src/ciu/deploy_pkg/phases.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-dead-code-removal
    observable: "action_build (deploy.py ~2456) and collect_bake_targets_from_selection's use by it are removed ONLY after confirming, at YOUR OWN commit's tip (not trusting this handoff's carve-time grep), that action_build has zero callers anywhere in src/ciu (grep the whole tree, not just deploy.py/cli.py) and that no CLI flag routes to it (grep '--build' across cli.py). collect_bake_targets_from_selection ITSELF is kept (it becomes the new `ciu bake --profile` path's target resolver -- see O2) and collect_bake_targets_from_phases is left untouched unless your own grep shows it too has zero callers (verify, don't assume from this handoff)."
    negative: "deleting collect_bake_targets_from_selection along with action_build (it is reused by O2 -- deleting it would just require rewriting the same logic); deleting anything with a live caller because a stale grep from carve time said otherwise"
    gate: "tester-unified"
  - id: O2-profile-resolution
    observable: "`ciu bake --profile NAME` resolves the selection via the EXACT SAME chain `ciu up --profile` uses (load_global_config -> resolve_profiles -> build_selection, all in deploy.py, already imported by cli.py's other verbs) then calls collect_bake_targets_from_selection(selection) to compute the target list, which is passed to the SAME `docker buildx bake ... --load` invocation the verb already builds (identical revision-stamping via engine.bake_revision_args(), identical --no-cache handling) -- only the TARGET LIST's source changes when --profile is given. `ciu bake [targets...]` with NO --profile is BYTE-IDENTICAL to today's behavior (raw positional targets straight to buildx, defaulting to 'all')."
    negative: "changing the no-profile invocation's behavior in any way (today's simplest form must keep working exactly as before -- this is an additive flag, not a replacement); resolving the profile selection differently than `ciu up --profile` does (a divergent resolution would make `ciu bake --profile X` build different targets than `ciu up --profile X` deploys, defeating the whole point of 'respecting the selection model')"
    gate: "tester-unified"
  - id: O3-flag-conflict
    observable: "--profile and explicit positional targets are mutually exclusive on `ciu bake` (mirror the existing `--layout`/`--host` mutual-exclusion error style at cli.py's `up` verb, prefix-aware per that precedent's B2 finding -- i.e. also catch `--profile=NAME`) with a clear stderr message and exit 2; --no-cache combines with either mode unchanged."
    negative: "silently ignoring positional targets when --profile is also given (which one wins is not obvious to a reader and invites a divergent-build bug); a bare `--profile` substring match that also fires on `--profile=NAME` inconsistently (copy the up verb's exact prefix-aware check, don't half-implement it)"
    gate: "tester-unified"
  - id: O4-docs
    observable: "README.md's existing feature line for bake (if any -- grep) or FEATURES.md's CLI table documents --profile. docs/SPEC.md documents the unified resolution (new clause or extend an existing S7/S8 section -- your call, state which in the LOG). CHANGES.md Unreleased entry notes action_build's removal as an internal-only cleanup (it had no CLI surface, so this is NOT a breaking change -- say so explicitly to avoid a false breaking-change alarm in release notes). docs/BACKLOG-2026-08-24.md CIU-QOL-7 row -> FIXED with evidence."
    negative: "describing action_build's removal as a breaking change when it had zero reachable callers (misleads a release-notes reader into thinking something they used is gone)"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "action_build turns out to have a live caller your own grep finds (contradicting this handoff's carve-time finding) -- BLOCKED naming the caller, do not delete a reachable code path"
  - "build_selection/resolve_profiles cannot be reached from ciu bake's argument-parsing context without importing something that creates a cycle with cli.py's existing import graph -- BLOCKED naming the exact cycle"
mutexes: [merge-lane]
review_focus:
  - "byte-identical no-profile behavior (diff the exact buildx argv construction before/after for the no-flag case)"
  - "the profile-vs-positional-targets conflict check actually catches `--profile=NAME` (equals form), not just `--profile NAME` (space form) -- this exact class of bug was found and fixed once already in the `up --layout` precedent (checkpoint C, 2026-08-20); don't reintroduce it here"
  - "action_build really had zero callers before deletion -- re-run the grep yourself at review time, don't trust the implementer's claim uncritically"
---

# ciu-P17 — unified `ciu bake` (CIU-QOL-7)

## Context to read first
1. `docs/BACKLOG-2026-08-24.md#CIU-QOL-7` (already in your context via
   `source`) — the ask: two non-communicating build paths exist; unify on
   the selection model, remove the internal one.
2. `src/ciu/cli.py` — the `bake` verb block (~1365-1369, right after `init`,
   right before `dev`): today's ENTIRE implementation is 6 lines — parse
   `--no-cache` and raw positional targets, build a `docker buildx bake`
   argv, append `engine.bake_revision_args()`, run it. This is the code your
   `--profile` branch sits alongside (not replaces) — the no-flag path stays
   this simple.
3. `src/ciu/deploy.py`:
   - `action_build` (~2456-2480+, read to its end) and
     `collect_bake_targets_from_selection` (~2912-2919) — read both in full.
     `action_build` is dead (verify yourself: grep the whole `src/ciu/` tree
     for `action_build(` and for `"--build"`/`'--build'` — at carve time
     neither had a live caller or CLI flag). `collect_bake_targets_from_
     selection` is NOT dead by association — it's a pure, reusable target
     resolver (`{final path component of applications/tools entries}`) and is
     exactly what your new `--profile` path needs.
   - `main`/`_run` (~2689-2746) for the exact `load_global_config` →
     `resolve_profiles` → `build_selection` chain — the one `ciu up --profile`
     already uses. Your `--profile` path must call this same chain, not a
     parallel one.
4. `src/ciu/cli.py`'s `up` verb `--layout` mutual-exclusion block
   (~1115-1148, look for `_LAYOUT_FORBIDDEN` and the prefix-aware `a == flag
   or a.startswith(flag + "=")` check, and its checkpoint-C fix history in
   this same block's comments) — copy this exact prefix-aware pattern for
   `--profile` vs. positional-targets mutual exclusion on `bake`.
5. `tests/tests/test_ciu_cli_bake.py` if it exists (grep) — otherwise the
   nearest analogous CLI verb test file for fixture style (fake
   `subprocess.call`/`procutil.docker`, never a real `docker buildx`
   invocation in tests).

## Work
1. Verify `action_build` is genuinely dead (O1); delete it (keep
   `collect_bake_targets_from_selection`).
2. Add `--profile NAME` to the `bake` verb: when given, resolve the selection
   via the shared chain and compute targets via
   `collect_bake_targets_from_selection`; when absent, today's exact
   positional-targets behavior (O2).
3. Add the prefix-aware `--profile` vs. positional-targets mutual-exclusion
   check (O3).
4. Update docs (O4).
5. Tests: no-profile byte-identical case, `--profile` resolving the same
   targets `build_selection` would deploy, the equals-form conflict check,
   `--no-cache` combining with both modes.
6. LOG at `nyxloom-trove/reports/ciu-P17-unified-bake-LOG.md`.

## Environment setup
Same worktree/venv as prior packages:
`cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu && .venv/bin/python run-ciu-tests.py`

## BLOCKED rule
Per `escalate_if` above. Forbidden workaround: deleting `action_build`
without re-verifying it is actually unreachable in the current tree.
