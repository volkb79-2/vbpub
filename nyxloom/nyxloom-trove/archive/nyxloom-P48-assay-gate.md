---
schema_version: 1
id: nyxloom-P48-assay-gate
project: nyxloom
title: "Assay-backed R0/R1 gate for nyxloom's own tester-unified lane (NL-1)"
tier: implement-1
input_revision: "a74bc6f6"
depends_on: []
session: fresh
source: {kind: backlog, ref: "nyxloom-trove/backlog/NL-1-assay-backed-implementation-gate-pinned-pyz-judge-replaces-cove.md"}
scope:
  touch:
    - ".gitignore"
    - "assay.toml"
    - "run-gate.toml"
    - "nyxloom-trove/nyxloom.toml"
    - "nyxloom-trove/backlog/NL-1-assay-backed-implementation-gate-pinned-pyz-judge-replaces-cove.md"
    - "nyxloom-trove/backlog/INDEX.md"
    - "nyxloom-trove/reports/nyxloom-P48-assay-gate-LOG.md"
    - "nyxloom-trove/reports/nyxloom-P48-assay-gate-REPORT.md"
  forbid:
    - "src/"
    - "tests/"
    - "tools/assay/"
oracles:
  - id: O1-vendor-integrity
    observable: "from nyxloom/, 'cd tools/assay && sha256sum -c assay-4.0.0.pyz.sha256' -> OK; the pinned zipapp reports 'assay 4.0.0' via --version."
    negative: "flip one byte of the vendored pyz (in a scratch copy, never committed) and re-run the gate: it fails at the sha256-verify step before any test executes -- non-zero exit, no test output."
    gate: tester-unified
  - id: O2-assay-verdict
    observable: "with a CLEAN tree (commit first -- assay's DIRTY_TREE check is fail-closed), 'cd nyxloom && ./run-gate.py --worktree <this worktree abs path> tester-unified' exits 0, prints the Assay PASS line, and leaves .assay/verdict-tester-unified.json naming lane tester-unified with R0 PASS and R1 PASS."
    negative: "delete/corrupt coverage.json before the judge step (a throwaway rerun, not part of your final state): the JUDGE reports an ERROR verdict, not a silent PASS -- the absence-for-pass trap NL-1 names explicitly."
    gate: tester-unified
  - id: O3-config-integrity
    observable: "nyxloom/assay.toml and nyxloom/run-gate.toml are both schema-valid: the live run in O2 loads them without a BAD_LANE_CONFIG refusal."
    negative: "in a scratch copy only, set judge.fail_under to a string (e.g. \"100\") instead of a float: the lane is refused ERROR/BAD_LANE_CONFIG at load, before any test runs -- proving the lane genuinely reads validated config, not a lightly-checked shell wrapper."
    gate: tester-unified
  - id: O4-no-regression
    observable: "nyxloom/src/nyxloom/coverage_gate.py and nyxloom/src/nyxloom/mutation_gate.py are BYTE-IDENTICAL to input_revision (git diff against a74bc6f6 for both paths is empty); the existing suite (tests/test_coverage_gate.py, tests/test_mutation_gate.py) still passes because nothing in src/ changed."
    negative: "a diff touching either file, or a broken mutation_gate.py import of coverage_gate's helpers, is a scope violation -- BLOCKED, not a workaround."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "the vendored pyz's sha256 does not verify against the sidecar you were handed"
  - "the new assay-judged gate cannot reach a real green on this worktree's clean HEAD for a reason your diff cannot fix"
  - "a needed change falls outside scope.touch, or requires touching a forbidden file"
---

# nyxloom-P48 — Assay-backed R0/R1 gate for nyxloom's own tester-unified lane

- Project: vbpub / nyxloom (self-hosted trove, dogfooding its own conventions)
- Tier: implement-1 (sonnet, fresh session) -- every value below is locked; no
  externally-visible decision is left for you to make. If you find one, that
  is a carve defect: STOP and write BLOCKED, do not invent the missing value.
- Authored at nyxloom's `input_revision` `a74bc6f6` (vbpub main, 2026-09-02).
  **Verify this is still the worktree's tip before you start** -- if it has
  drifted, that is itself worth a one-line LOG note, not a blocker.
- Worktree: already created and pinned to you --
  `/workspaces/vbpub/.worktrees/nyxloom-P48-assay-gate` (branch
  `feat/nyxloom-P48-assay-gate`, its own docker network). Work ONLY there;
  commit on the branch; do NOT merge (the controller merges after review).
- The carver has ALREADY committed the vendored Assay release into this
  worktree (`nyxloom/tools/assay/assay-4.0.0.pyz` + `.sha256`, downloaded
  from the `assay-v4.0.0` GitHub release and sha256-verified) as the carve
  commit. `nyxloom/tools/assay/` is therefore in `forbid` -- it is already
  correct; touching it is out of scope.

## Why this package exists

nyxloom's own gate is the last self-judged one in the estate: `ciu` and
`cmru` both judge their gates with a vendored, sha256-pinned Assay zipapp;
nyxloom still pipes pytest-cov straight into its own in-process
`nyxloom.coverage_gate` module. This package flips nyxloom onto the same
pattern `ciu` already proved in this exact repo -- concretely, you are
mirroring three files ciu already ships, substituting `ciu` -> `nyxloom` /
`tester-unified` and nyxloom's own facts. `coverage_gate.py` and
`mutation_gate.py` are NOT touched: `mutation_gate.py` imports
`coverage_gate`'s helpers directly (`_resolve_base`, `_check_measurable`,
`_git_added_lines`, `NoMeasurementError`, `CoverageGateError`) and stays on
the old self-judged path (R2/mutation is an explicit, separate NL-1
fast-follow, not this package). Only the gate's OWN invocation of
`python -m nyxloom.coverage_gate` is removed -- and it is removed simply by
no longer being in the new argv, not by editing the module.

## Context to read first (exact order)

1. `nyxloom-trove/backlog/NL-1-assay-backed-implementation-gate-pinned-pyz-judge-replaces-cove.md`
   -- the backlog entry this package resolves. Whole file (it is short).
2. `../ciu/assay.toml` (sibling project in this worktree, one level up from
   `nyxloom/`) -- the EXACT template for the new `assay.toml`. Read every
   line and its comments; you are producing nyxloom's analog, not a
   reinterpretation.
3. `../ciu/run-gate.toml` -- the EXACT template for the edited
   `run-gate.toml` `kind="assay"` lane.
4. `../ciu/nyxloom-trove/nyxloom.toml` -- read only the
   `[gates.tester-unified]` block, for the `asserts` line shape (the argv
   in YOUR copy is already correct and needs no change).
5. `../ciu/.gitignore` -- read only the `tools/assay` exception (bottom of
   file) for the exact comment/line shape to mirror.
6. `nyxloom/run-gate.toml` and `nyxloom/nyxloom-trove/nyxloom.toml` (YOUR
   copies) -- the current state you are editing.
7. `nyxloom/src/nyxloom/coverage_gate.py` -- read ONLY the module docstring
   and the `fail_under` default (`= 100.0`, the argparse `--fail-under`
   default) -- confirms the floor value below is not invented.
8. `docs/backlog-entries-spec.md`, the `carved_handoff` field description
   (search for that string) -- the one field you add to NL-1's frontmatter.

Do not read `ciu/src/`, `ciu/tests/`, or anything under `assay/` -- nothing
there is relevant and it is out of scope regardless.

## Work

### W1 — `nyxloom/.gitignore`
Root `.gitignore`'s `*.py[codz]` pattern incidentally matches `*.pyz` (the
`z` in the character class). Append, mirroring `ciu/.gitignore`'s own
exception verbatim in spirit (adjust wording to nyxloom, keep the mechanism
identical):

```
# Assay's pinned Python zipapp is an executable release artifact, not
# Python bytecode -- carve it out of the root `*.py[codz]` ignore rule
# (mirror of ciu/.gitignore's `!tools/assay/*.pyz`, cmru precedent).
!tools/assay/*.pyz
```

### W2 — `nyxloom/assay.toml` (new file)

Create with EXACTLY this content (only the header comment prose may be
lightly copy-edited for nyxloom voice if you wish -- every key/value below
is locked, do not change one):

```toml
# nyxloom's own implementation gate claim (NL-1 -- Assay-backed judgment,
# replacing the retired `nyxloom.coverage_gate` self-judgment).
#
# The lane command is nyxloom's existing pytest invocation (pytest-cov, NOT
# `coverage run` -- coverage run traces only the parent, measuring ~nothing
# of xdist workers, which would false-FAIL under `-n auto`). R1 makes Assay
# JUDGE that coverage: the changed-line floor on base..HEAD (the role the
# retired `nyxloom.coverage_gate` used to play) and a verifiable verdict
# artifact. tester-unified supplies the environment; the pinned zipapp
# (tools/assay/assay-4.0.0.pyz, sha256-verified) is the Assay CLI boundary --
# Assay source is never imported, and `python -m nyxloom.coverage_gate` no
# longer runs from the gate (the module itself stays: `mutation_gate.py`
# still imports its helpers, and it remains the toolkit nyxloom OFFERS other
# projects per STANDARD.md -- unrelated call sites, out of this package).
#
# require_branch = false: nyxloom's suite does not measure branch coverage
# today (no --cov-branch, no [tool.coverage] config) -- this migration swaps
# the JUDGE for the existing line-only floor (D-064-L2); it does not raise
# the bar. Declaring require_branch=true here would hard-fail every run on
# an unmeasured property. Widening to branch coverage is a separate decision.
#
# snapshot_selection = "repository-minus-unsafe-symlinks": this assay.toml
# lives in the same vbpub monorepo as ciu/assay.toml, whose repository-wide
# snapshot includes topos's three absolute-symlink security fixtures --
# declared verbatim below, byte-identical to ciu/assay.toml's declaration
# (same repo, same fixtures, same reason).
schema_version = 2

[lanes.tester-unified]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["/opt/tester-venv/bin/python", "-m", "pytest", "tests", "-n", "auto", "-q", "--cov=src/nyxloom", "--cov-report=json:coverage.json"]
env = { PYTHONPATH = "src", PYTHONDONTWRITEBYTECODE = "1" }
# PATH: xdist worker spawn (subprocess). HOME: config/cache dirs some tests
# touch. TERM/LANG: parity with ciu's lane. No CGROUP_PARENT_DEV_BACKGROUND
# passthrough -- nyxloom's suite (unlike ciu's cgroup-governance tests) never
# reads it.
env_passthrough = ["HOME", "PATH", "TERM", "LANG"]
budget = "30m"
allow_argv_append = false

[lanes.tester-unified.isolation]
snapshot_selection = "repository-minus-unsafe-symlinks"
unsafe_symlink_omissions = [
  "topos/tests/fixtures/inspect_files/_danger/passwd_link",
  "topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape",
  "topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current",
]

[lanes.tester-unified.judge]
language = "python"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
require_branch = false
base = "origin/main"

[lanes.tester-unified.judge.coverage]
format = "coverage-py-json"
artifact = "coverage.json"
```

### W3 — `nyxloom/run-gate.toml`

Replace the file's ENTIRE content with:

```toml
# =============================================================================
# nyxloom project gate lanes -- parsed ONLY by run-gate.py (one parser, D-110).
# Environment facts come from the CENTRAL vbpub-root run-gate.toml (nearest
# ancestor); judgment policy lives in assay.toml [lanes.tester-unified] (R0+R1:
# the existing pytest-cov suite, D-064-L2 changed-line floor). NL-1 landed
# here (nyxloom-P48): judgment moved from `nyxloom.coverage_gate`
# self-judgment to Assay's own R1 changed-line judge -- ciu/run-gate.toml is
# the proven precedent this mirrors. See run-gate-project/SPEC.md.
# =============================================================================
schema_version = 1

[lanes.tester-unified]
kind = "assay"
assay_lane = "tester-unified"        # -> assay.toml [lanes.tester-unified]
environment = "tester-unified"       # central [environments.tester-unified]
assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-4.0.0.pyz"]
# -n auto is the measured optimum on this 8-core host (~2m28s vs 4m56s
# serial); pytest-cov (NOT `coverage run`) is load-bearing under xdist for
# the same reason it always was -- coverage run traces only the parent,
# measuring ~nothing of xdist workers. Both facts now live in assay.toml's
# argv (the judged command), not here.
budget = "30m"                       # advisory; consumers may enforce

[lanes.tester-unified.pins.assay]
version = "4.0.0"
sha256 = "tools/assay/assay-4.0.0.pyz.sha256"
```

Note what is DELETED and not replaced: `kind = "command"`, the old inline
argv, and the `clean_tree = false` key + its comment. Dropping `clean_tree`
entirely falls back to the schema default (`true`) -- exactly what
`ciu/run-gate.toml` does (it declares no `clean_tree` key at all). Do not
write `clean_tree = true` explicitly; omit the key, matching ciu.

### W4 — `nyxloom/nyxloom-trove/nyxloom.toml`

In `[gates.tester-unified]`, the `argv` line is ALREADY correct
(`cd {worktree}/nyxloom && ./run-gate.py --worktree {worktree} tester-unified`)
and needs no edit. Two changes only:

1. Add one line, `asserts = ["tests-pass", "changed-line-coverage", "canary-verified", "assay-verdict"]`,
   placed after `phase = "implementation"` and before `timeout_seconds`
   (mirroring `ciu/nyxloom-trove/nyxloom.toml`'s exact line order).
2. Replace the four-line comment block above `argv` (the one ending "NL-1
   migrates judgment to assay ... retiring coverage_gate self-judgment")
   with a landed-state version, e.g.:
   ```
   # SSOT pointer (D-110/D-111): all test definitions live in
   # nyxloom/run-gate.toml; this entry owns only scheduling policy.
   # run-gate resolves the slice from $CGROUP_PARENT_DEV_BACKGROUND,
   # dual-mounts the repo, and runs the pinned assay lane
   # (assay.toml [lanes.tester-unified]) in tester-unified. NL-1 landed
   # 2026-09-02 (nyxloom-P48): judgment moved off `nyxloom.coverage_gate`.
   ```
   Keep every other line (`argv`, `phase`, `timeout_seconds`, `environment`)
   untouched.

### W5 — `nyxloom-trove/backlog/NL-1-...md`

Add `carved_handoff: nyxloom-P48-assay-gate` to the frontmatter (see
`docs/backlog-entries-spec.md`'s field description for the exact placement
convention -- it sits alongside `filed_date`). Then run, from
`nyxloom/`: `nyxloom backlog set-status NL-1 carved`. Regenerate the index:
`nyxloom backlog index`. Both commands operate on trove files relative to
your CWD; run them from inside `nyxloom/` in this worktree, not the primary
checkout.

## Environment setup (recipe -- run it yourself, nothing is pre-built)

From `nyxloom/` in this worktree:
1. Make your edits (W1-W5) and commit them (`git add -A -- .gitignore
   assay.toml run-gate.toml nyxloom-trove/nyxloom.toml
   nyxloom-trove/backlog/`, one commit).
2. Confirm a clean tree: `git status --short` from the worktree root must be
   empty before the live gate run (assay's DIRTY_TREE check fails closed).
3. Run the live gate: `cd nyxloom && ./run-gate.py --worktree
   /workspaces/vbpub/.worktrees/nyxloom-P48-assay-gate tester-unified`.
   This launches the tester-unified container itself (§4 of AUTHORING.md --
   never the cockpit) -- you do not orchestrate docker by hand.
4. Read the verdict in a SEPARATE step from the run (never trust a piped
   tail): `cat .assay/verdict-tester-unified.json` after the command exits,
   and separately echo `$?` from the run itself. Paste both into your LOG.

## Oracles

See frontmatter `oracles` (O1-O4) for the checkable claims. For O1 and O3's
negative halves, do the corruption in a SCRATCH COPY outside the repo (e.g.
`/tmp`) or in an immediately-reverted uncommitted edit -- never leave a
corrupted pin or a broken TOML in your final commit. Record the exact
command and its exit code / verdict content for every oracle in your REPORT.

**Anti-pattern list (paste-in, per AUTHORING.md §3b)** -- applies to nothing
you're writing here (no new test files), included only so you recognize it
if you touch `nyxloom/nyxloom-trove/reports/` prose: no wall-clock
deadlines, no hollow assertions, no coverage-evasion pragmas, no live
network/clock dependence in anything you claim as evidence.

## Deliberately deferred (not this package -- do not attempt)

- R2 mutation-as-assay-claim (NL-1's own item 3) -- `mutation_gate.py` stays
  exactly as-is, still self-invoked wherever it already is today (nowhere in
  this gate's argv, unchanged).
- Reconciling `gate_canary.py` with assay's `canary.py`/R3 -- NL-1's prose
  mentions this, but ciu's own adoption (the working precedent) never did
  it: ciu's `canary-verified` assert is nyxloom's OWN periodic `nyxloom gate
  verify` mechanism (STANDARD.md), orthogonal to assay's rigor ladder, and
  it needs zero changes here. Do not touch `gate_canary.py`.
- Proving R1 rejects a genuinely uncovered new source line end-to-end (as
  opposed to O2's artifact-absence proof) -- valuable, but requires a
  throwaway edit inside forbidden `src/nyxloom/`, which NL-1's own oracle
  list never actually requires (re-read it: it asks for the sha256/absence/
  mutation proofs above, not a live uncovered-line injection). Left as a
  reviewer's discretionary spot-check, not an implementer oracle.

## Scope

See frontmatter `scope.touch` / `scope.forbid`. If satisfying an oracle
seems to require a forbidden file or a file outside `scope.touch`, that is
the BLOCKED trigger below -- do not improvise past it.

## Evidence discipline

LOG chronological including any failed attempt; every claim in the REPORT
names its exact command and verbatim (or clearly truncated) output; the O2
live run's PASS line and verdict JSON excerpt go in both LOG and REPORT.
Final message: tip commit sha, O1-O4 status table, the live gate's verbatim
exit code and verdict summary.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a
forbidden file, STOP: write `BLOCKED: <reason>` to
`nyxloom-trove/reports/nyxloom-P48-assay-gate-LOG.md`, commit the branch
as-is, and end your final message with `BLOCKED: <one-line mechanical
reason>`. Product calls are the controller's (D-numbered), never yours --
you should not need to raise one here; everything is pinned.
