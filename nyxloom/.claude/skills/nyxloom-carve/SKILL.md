---
name: nyxloom-carve
description: Author a nyxloom package handoff (AUTHORING-compliant) — contract shape, oracle rules, scope/forbid discipline, lint+freeze loop. Use when carving a new package or repairing a carve after review, in ANY nyxloom-registered project.
---

> **Tool versions as of last verified update (2026-09-17):** nyxloom `0.6.0`,
> run-gate `23.8.0` (pip-installed), ciu `7.13.2`.
> `assay` pip package is `6.3.0` (verdict schema v11, current — see
> `assay/docs/CONSUMERS.md` "Migration notes (v10 → v11)"). A consuming
> project's own gate lanes may still pin an older frozen `assay-*.pyz`
> artifact in `run-gate.toml`'s `[lanes.*.pins.assay]` blocks — that pinned
> artifact, not the installed pip package, is what a lane actually runs, so
> check both before assuming the newer schema applies. Re-pinning a project
> to a newer `assay-*.pyz` is a real, bounded, cross-lane task — do it as its
> own serial step, never mid-flight alongside another package's active gate.
> If any CLI verb/flag below errors, check `nyxloom --version` / `pip show
> run-gate` / `python3 -c "import ciu; print(ciu.__version__)"` against these
> before assuming the skill text is still right — re-verify and bump this
> line rather than patching around a mismatch silently.

> **Canonical, repo-agnostic skill.** The pipeline SHAPE is universal to any
> nyxloom-registered project; substitute the target repo's own trove paths,
> package-id prefix, and gate argv from its own CLAUDE.md/AGENTS.md. Examples
> below use dstdns's conventions (`dstdns-P<NNN>`, `/workspaces/dstdns`) —
> read them as illustrations, never run a dstdns gate line against another
> repo.

> **MANDATE — assert requirements, never predicted measurements.** A handoff
> states the POLICY requirement (full branch coverage, the project's R0-R3
> testing tiers, a design decision) as a contract item — it never hand-computes
> what a coverage/mutation tool will eventually report. That number does not
> exist until the tool is run against real (or synthetic stand-in) code; a
> carver reasoning about a rendered coverage report instead of executing
> `assay`/`coverage.py` against a stand-in has, more than once, undercounted a
> branch arc the report's own display suppresses (see AUTHORING.md §3b(F)). If
> a carve needs to justify "this will be coverable" or "this branch is
> unreachable", the carve is making a claim it must not make — replace it with
> the policy requirement itself and let the implementer's own gate run prove it.

# Carving a package handoff

Target: `<trove>/handoffs/<repo>-P<NNN>-<slug>.md` (dstdns example:
`nyxloom-trove/handoffs/dstdns-P<NNN>-<slug>.md`). Read
`nyxloom/reference/AUTHORING.md` first (canonical contract); this skill is the
cross-project mechanics distilled from dstdns's P106–P112.

## Before writing
1. **Measure, never assume.** Every deletion/change claim needs a tabulated sweep
   (D-128 #10): symbol-level, ALL tracked file types (py/toml/j2/ts/js/sh/yml/md),
   `git grep` so only tracked files count, results TABULATED in the carve (file |
   line | disposition). A grep executed but not tabulated is an assertion.
2. **Forbid entries are sweep claims too** (D-133 lesson): a forbid rationale saying
   "zero references" must be backed by the same tabulated sweep.
3. **Reverse-dependency sweep** for everything deleted/moved: callers, importers,
   renderers, re-exports (flat-shim layers: `common/__init__.py`, shim manifests —
   grep the BARE name).

## Frontmatter (machine-read; nyxloom lint gates it)
- `input_revision`: a real hash. Two-step freeze: commit the carve, then a second
  commit setting input_revision to the first commit's hash. Re-freeze after every
  repair round — the pin can only be set correctly ONE commit after the repair,
  since the repair's own hash doesn't exist yet when it lands.
- `title`: short (lint rejects long ones).
- `scope.touch`: every file an oracle or contract item needs — an oracle
  unsatisfiable within scope.touch is the P26/P31 killer. Directory sweeps
  (`tests/`, `docs/`) are legitimate entries; annotate each entry with WHY.
- `oracles`: each has `observable` (mechanically checkable, names the gate) and
  `negative` (what does NOT count). Rules: pin BOTH halves of any regex (or specify
  by must-catch/must-pass example pairs); when an oracle states a family property
  with an exclusion, say WHICH DIMENSION the exclusion narrows; bound scan roots IN
  the frontmatter and itemize named negatives; require MUTATION-CHECKED for guards.
- `escalate_if`: mechanical BLOCKED conditions + the checkpoint clause (arm at the
  project's own measured threshold — see CLAUDE.md "Long-running agent context
  discipline" for the currently-live figure, do not hardcode an old one here). Name
  BOTH checkpoint artefacts as authorised paths — the BRIEF and the self-authored
  `/compact` retention prompt — or the code reviewer flags the second as an
  unlisted touch (P116 N4).

## Body (must agree with frontmatter — diff BOTH halves, D-129)
- Branch line: `p<NNN>-<slug>`, worktree `<repo-root>/.worktrees/<branch>`.
- BLOCKED protocol paragraph (lint requires the literal `BLOCKED:` marker).
- "Context to read first" with exact files/sections in order.
- Numbered contract items; Environment setup (Mode-B: package image tags
  `--set <svc>.tags=<repo>/<svc>:p<NNN>`, NEVER `:latest`; GUIDE §3.3 both-prefix
  teardown).
- Gate argv verbatim: mock lane from MAIN repo root under
  `eval "$(ciu env print)" && flock "/tmp/${REPO_NAME}-${INSTANCE_ID}-testrunner.lock"`
  (keys off ciu's own instance identity, not worktree/branch name); schema gate
  takes an explicit path. **RG-39 shipped 2026-09-03 (run-gate 23.5.0, rev 35):**
  run-gate now takes its own internal exec-mode mutex keyed by the resolved
  container name, so this caller-side `flock` is no longer load-bearing for
  correctness — two consumers resolving to the SAME container now serialize
  automatically with no caller coordination. Keep using it anyway (it stays valid
  as an outer lock, cheap, and gives pre-emptive scheduling — skip a busy
  container rather than block inside it); just don't treat its absence as a bug
  the way earlier sessions had to.
- **Never** hand-run the suite via a bare `pytest`/local venv, and **never** stand
  up the stack via raw `docker compose`/`git worktree add` when `run-gate <lane>` /
  `ciu worktree` is available — those are the tool-first replacements this pipeline
  exists to enforce.

## After writing
1. `nyxloom lint <handoff>` — read the FULL output, not the tail (errors can hide
   above warnings). Directory-prefix and named-negative path warnings are known
   false positives; verify each once.
2. Commit + freeze (two-step).
3. **Fresh adversarial carve review before EVERY dispatch** (never a fork). On
   REJECT: dispositions → ledger D-record → repair BOTH halves → re-lint →
   re-freeze → fix-verification by the SAME reviewer → dispatch only on its
   unambiguous word.
