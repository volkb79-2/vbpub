# run-gate-WAVE-RG55-P2 — BRIEF-2 (successor continuation)

Checkpoint cut per E-008, at a coherent boundary: two green-gate-adjacent
commits landed (C1-rework, C2 vendoring), no code left uncommitted,
LOG/REPORT updated, one real blocking decision ask surfaced with full
evidence rather than silently worked around. Written for a FRESH successor
— you have no memory of this session.

## Where things stand

Worktree: `/workspaces/vbpub/.worktrees/rg55-run-gate-client`, branch
`rg55-run-gate-client`, project dir `run-gate-project/`. Work ONLY there —
**this is the SAME hazard BRIEF-1 flagged**: `/workspaces/vbpub/run-gate-project/`
(no `.worktrees/` in the path) is the MAIN checkout and looks identical.
Double-check the absolute path before every Edit/Write; after any batch of
edits, `git -C /workspaces/vbpub status --porcelain -- run-gate-project/`
must show nothing (this session verified it clean after every commit —
also check `.gitignore` at the worktree root if you touch it, same trap,
confirmed a separate file from the main checkout's copy this session).

Git log at hand-off (`git -C /workspaces/vbpub/.worktrees/rg55-run-gate-client log --oneline -5`):
```
f687a4ed chore(rg55-p2): vendor assay-6.1.1.pyz for run-gate-project's assay lanes
8c76ba3e fix(rw5): coverage_gate.py 0/0 diff reports SKIPPED, not refused or silently OK
1dc201ab docs(rg55-p2): checkpoint after C1 -- LOG, REPORT, BRIEF-1 for successor
607950fd fix(rg53): coverage_gate.py reads missing_branches, refuses a 0/0 diff
e499a168 plan(rg55): controller log -- record the P0 freeze commit
```

**C1-rework (RW-5) is DONE.** `tools/coverage_gate.py`'s 0/0 handling now
reports `diff-coverage SKIPPED: ...` (exit 0) by default, naming the
base/HEAD relation; `--refuse-empty-diff` (opt-in) reproduces the old
exit-2 refusal; `Verdict` gained `skipped`/`verdict` fields.
`tests/test_coverage_gate.py`: 23 passed. Whole-suite `./run-gate.py
selftest --allow-dirty`: 830 passed, 3 skipped (pytest), diff-coverage
phase prints SKIPPED (2 commits ahead of `main`, neither touching
`run-gate.py`), lane exit 0. Full detail: LOG's "Commit 2 — C1-rework
(RW-5)" and REPORT's "C1-rework — RW-5" section.

**C2 vendoring is DONE.** `tools/assay/assay-6.1.1.pyz` + `.sha256`
copied from `cmru/tools/assay/`, sha256-verified, `.gitignore` negation
added, `git ls-files` confirms both tracked.

**C2's `[lanes.r1]`/`[lanes.r2]` judge tables are BLOCKED on a decision
this session did NOT make — read this before writing any `assay.toml`
judge table:**

The handoff wants a Python judge scoped to `run-gate.py` only, excluding
`tests/`/`tools/`, on a `base_source = "request"` (changed-lines) lane.
This is genuinely not expressible in Assay's current schema when
`run-gate.py` has no isolating subdirectory of its own (it sits at the
project root beside `tests/` and `tools/`):
- `judge.source_roots` must be a directory (`assay/src/assay/config.py`
  `_resolve_source_root`, ~line 3369: `if not resolved.is_dir(): raise
  LaneConfigError`).
- `judge.targets` (the only file-level scoping key) is legal ONLY under
  `mode = "whole_target"` (`config.py` ~line 2114), which FORBIDS
  `judge.base`/`base_source` entirely (`assay/docs/CONSUMERS.md`'s
  `redirect_chain` example + the `base_source` refusal table) — mutually
  exclusive with the handoff's `base_source = "request"` requirement.
- Python's `excluded_dir_names` (`adapters/python.py` line 805) is a fixed
  EMPTY frozenset, not lane-configurable (unlike JS/SQL adapters, which DO
  exclude directories at the adapter level).
- `tests/` is already excluded automatically (`evaluate.py::_is_considered`
  calls `adapter.is_test_path`, ~line 427) — no declaration needed.
- `tools/` is NOT a recognized test path. With `source_roots = ["."]` and
  a coverage command scoped to `--cov=run_gate` only, any future commit
  touching `tools/coverage_gate.py` (which THIS session's own C1-rework
  did) would be "considered" but absent from the coverage artifact —
  `evaluate.py` ~line 519-527 counts that as changed-and-100%-uncovered,
  not skipped — so `assay-r1` would fail on every such commit, forever,
  with no config fix available.

**You must pick one before writing `[lanes.r1.judge]`/`[lanes.r2.judge]`:**
1. Measure `tools/` for real: `--cov=run_gate --cov=tools --cov-branch`.
   Satisfies the underlying intent (no false-refusal trap); contradicts the
   handoff's literal "tools/ never judged" wording; `tools/coverage_gate.py`
   changes then need real coverage to pass `assay-r1`/`r2` (same bar as
   `run-gate.py`, arguably correct since it is real shared code, as this
   session's 8 new tests for it demonstrate).
2. Restructure so `run-gate.py` lives in its own subdirectory, isolating
   it from `tools/` for `source_roots` purposes. A real repo-layout change
   (the `run_gate.py` symlink, `cmru.toml`, every doc/consumer path
   assumption) — likely out of this package's scope alone; no ruling
   authorizes it; flag to the controller rather than doing it unilaterally.

If the controller has not weighed in by the time you read this, default to
**option 1** (it is reachable without a scope-expanding decision, and
"real coverage measurement" is a safer failure mode than "silently wrong
exclusion") and record that choice, with this reasoning, in the REPORT's
decision-ask section — do not leave it unresolved a second time. Whichever
you pick, the REST of C2 does not depend on it and can proceed in parallel
or first: `[lanes.r1]`/`[lanes.r2]`'s non-judge fields (`argv`, `env`,
`budget`, `isolation`), `[lanes.assay-r3]` (the canary lane — read
`cmru/tools/coverage_canary.py` AND
`scripts/cgroup-profiler/tools/canary-run.sh`, NOT read yet this session,
pick one shape and say why per the original handoff), `run-gate.toml`'s
`[lanes.assay-r1]`/`[lanes.assay-r2]`/`[lanes.assay-r3]`/`[lanes.gate-full]`
wiring, `doctor`'s pass-for-new-lanes check, README's "Gate and evidence"
paragraph.

## What NOT to re-read

Everything BRIEF-1 already marked read/skipped stays that way (C1's own
scope, the interface contract's §3/§7, fixtures README, `SPEC.md` R-29/
R-36/R-39/R-40/R-41/R-42, all the `run_gate.py`/`test_run_gate.py` line
ranges for C3 — none of that changed this session). Additionally, this
session read (do NOT re-read): `cmru/run-gate.toml`'s lane names (assay,
coverage, mutation, canary, gate — did not read their bodies in detail,
only names, so a closer read of `[lanes.canary]`'s body specifically is
still open if you need the canary shape), `cmru/assay.toml` (whole, short),
`scripts/cgroup-profiler/assay.toml` (whole), `assay/README.md`'s "How it
works" section + the worked `[lanes.unit]` example, `assay/docs/
CONSUMERS.md`'s monorepo JS lane example, the `judge.targets`/
`whole_target` worked examples (`redirect_chain`, `harness_lib`), the
`base_source`/`--request-base` section in full, and the exact source of
`assay/src/assay/config.py` (`_KNOWN_JUDGE_FIELDS`, `JUDGE_FIELDS_BY_RIGOR`,
`_resolve_source_root`) and `assay/src/assay/evaluate.py`
(`_is_considered`, `evaluate_coverage`'s main loop) cited above. Still NOT
read: `cmru/tools/coverage_canary.py`, `scripts/cgroup-profiler/tools/
canary-run.sh`, `assay/docs/CONSUMERS.md`'s "cmru / tester-unified
integration" section (for how `run-gate.toml`'s `assay_command`/
`pins.assay` keys should look — infer from `cmru/run-gate.toml`'s
`[lanes.assay]` if that section isn't reached first).

## Host load state

No gate containers currently live (this session ran the whole-suite
selftest once, bare-host, no container; it completed and nothing was left
running). Zero assay lanes have been run yet — `assay-r1`/`assay-r3` are
still owed "once before return" per the original handoff once C2's lanes
exist; `assay-r2` "once at the very end" remains fully unstarted.

## Process notes

- Edit tool only; `git -C <worktree> commit -F <msgfile> --only --
  <paths>`; both trailers (this session used `Claude Sonnet 5
  <noreply@anthropic.com>` per the live session's own attribution
  instruction, which superseded the older `Claude Fable 5.1` text embedded
  in the original handoff/C1 commit — match whatever YOUR session's own
  current attribution instruction says, don't copy either prior commit's
  trailer blindly).
- `git ls-files run-gate-project/tools/assay/` after any further add there
  (already confirmed once this session; re-check if you add more).
- Checkpoint (E-008): ARM at ~120k context or ~60 tool calls, CUT at the
  next coherent boundary, write `-BRIEF-3.md`, update LOG/REPORT, commit,
  return.

## Retention prompt (paste into your own `/compact` if you need to compact mid-C2/C3)

```
KEEP: the C2 judge-table scoping decision (which option was picked, and
why) once made; the RG55 interface contract's §3/§7 exact field
names/formulas for whichever golden(s) you are mid-implementing against;
the file:line seams in run-gate.py for await_container/run_exec_lane/
follow_container/promote_follower once located; which of C2/C3/C4/C5/C6/
C7/C8 are done vs in-progress vs not-started, with commit hashes for each
done one; any new decision asks raised and their resolution or open
status; HOST LOAD container-count state; the assay-r1/r2/r3 run status
(none run as of this brief).
DROP: the full text of the interface contract/fixtures README/assay
config.py excerpts (all re-readable in one call from disk); the C1-rework
RW-5 implementation detail once you've confirmed it's still green (just
keep "RW-5 done, commit 8c76ba3e" as the pointer); the full assay-scoping
investigation narrative once the decision is actually made and recorded in
the REPORT — keep only the decision and its one-line rationale.
```
