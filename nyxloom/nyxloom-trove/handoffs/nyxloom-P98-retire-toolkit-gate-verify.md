---
schema_version: 1
id: nyxloom-P98-retire-toolkit-gate-verify
project: nyxloom
title: "Retire the coverage/mutation/canary toolkit + GA1/GA4 gate-verify feature"
tier: implement-2
input_revision: "73887702"
depends_on: []
session: fresh
source:
  kind: roadmap
  ref: nyxloom-trove/reports/ASSAY-NYXLOOM-REORIENTATION-2026-08-17.md
scope:
  touch:
    - "src/nyxloom/coverage_gate.py"              # delete
    - "src/nyxloom/mutation_gate.py"               # delete
    - "src/nyxloom/gate_canary.py"                 # delete
    - "tests/test_coverage_gate.py"                # delete
    - "tests/test_mutation_gate.py"                # delete
    - "tests/test_gate_canary.py"                  # delete
    - "tests/test_gate_verify_cadence.py"          # delete
    - "src/nyxloom/cli.py"                         # remove cmd_gate_verify + its argparse wiring + help text
    - "src/nyxloom/effects_gates.py"               # remove gate_canary import; verify_gate/_run_verify_probe/drain_verify; verify_running/verify_results state; the GATE_VERIFY_RECORDED effect registration
    - "src/nyxloom/daemon.py"                      # remove gate_canary from the effects_gates import list; remove _days_since_gate_verify + its call site
    - "src/nyxloom/reconcile.py"                   # remove VerifyGate Action class; remove module-contract item 16 (scheduling logic + docstring); remove days_since_gate_verify from ReconcileInput
    - "src/nyxloom/rules_attention.py"             # remove the gate-verify-cadence-overdue attention rule
    - "src/nyxloom/types.py"                       # remove EventType.GATE_VERIFY_RECORDED
    - "src/nyxloom/config.py"                      # remove GateDef.gate_verify_interval_days field + its comment; ADD "assay-verdict" to the asserts-enum comment (NL-4)
    - "src/nyxloom/onboarding_gate.py"             # drop the "ecosystem coverage_gate.py" + "nyxloom gate verify" mentions from the missing-gate guidance text
    - "src/nyxloom/gate_scaffold.py"               # drop the `python -m nyxloom.coverage_gate` line from the scaffolded argv; asserts=["tests-pass"] (drop changed-line-coverage, no longer measured); point the ADJUST_MARKER comment at run-gate/assay adoption instead
    - "src/nyxloom/schemas/nyxloom-config.schema.json"  # NL-4: add "assay-verdict" to the asserts enum array
    - "nyxloom/reference/STANDARD.md"              # remove the "OFFERED, not mandated -- the toolkit" section; remove the `nyxloom gate verify`/GA1 paragraph; add "assay-verdict" to the asserts-enum prose list (NL-4)
    - "nyxloom/assay.toml"                          # update the header comment: it currently claims mutation_gate.py "remains the toolkit nyxloom OFFERS other projects" -- false once deleted
    - "nyxloom-trove/decisions.md"                  # new dated decision record (see Work item 8)
    - "nyxloom-trove/reports/ASSAY-NYXLOOM-REORIENTATION-2026-08-17.md"  # already imported+annotated by the carver; read-only for the implementer
    - "nyxloom-trove/handoffs/nyxloom-P90-extract-testing-library.md"  # move to nyxloom-trove/archive/, prepend a superseded note
    - "nyxloom-trove/archive/nyxloom-P90-extract-testing-library.md"    # the move destination
    - "tests/test_cli.py"                          # remove/adjust references to cmd_gate_verify / `gate verify`
    - "tests/test_daemon.py"                       # remove references to VerifyGate/_run_verify_probe/GATE_VERIFY_RECORDED/gate_canary; KEEP the six test_mutation_gate_* functions (~line 6136-6390) unchanged -- they test effects_merge.py's generic phase='mutation' re-run wiring, which this package does NOT touch or delete
    - "tests/test_effects.py"                      # remove references to VerifyGate/GATE_VERIFY_RECORDED/drain_verify
    - "tests/test_invariants.py"                   # remove references to VerifyGate/GATE_VERIFY_RECORDED
    - "tests/test_planning.py"                     # remove references to VerifyGate/gate_verify_interval_days/days_since_gate_verify
    - "tests/effect_differential.py"               # remove references to VerifyGate/GATE_VERIFY_RECORDED/drain_verify
    - "tests/legacy_planner.py"                    # remove references to gate_verify_interval_days/days_since_gate_verify if present
  forbid:
    - "src/nyxloom/gate_runner.py"       # generic gate-argv executor; stays, becomes the ONLY gate-execution path
    - "src/nyxloom/effects_merge.py"     # the ("mutation", cfg.policy.mutation_gate, effects_gates.select_mutation_gate) wiring is GENERIC (picks a project-DECLARED phase='mutation' GateDef, never imports the deleted module) -- confirmed by reverse-dependency sweep (Context item 4); do not touch
      # zero references to coverage_gate/mutation_gate-the-module/gate_canary in this file: `git grep -n "coverage_gate\|gate_canary" -- src/nyxloom/effects_merge.py` returns nothing; its only "mutation_gate" hit is the `cfg.policy.mutation_gate` attribute read at line 134, the kept boolean toggle, not a module reference
    - "nyxloom-trove/nyxloom.toml"        # its own [gates.tester-unified] argv already runs run-gate.py (P48); it declares no phase='mutation' gate today, so nothing here references a deleted module -- no edit needed
oracles:
  - id: O1
    observable: >-
      `git ls-files` from the repo root shows NONE of: src/nyxloom/coverage_gate.py,
      src/nyxloom/mutation_gate.py, src/nyxloom/gate_canary.py, tests/test_coverage_gate.py,
      tests/test_mutation_gate.py, tests/test_gate_canary.py, tests/test_gate_verify_cadence.py.
    negative: >-
      Any of the seven paths still tracked (including under a renamed/moved path) is a
      failure -- this is a closed, named list, not a pattern sweep.
    gate: tester-unified
  - id: O2
    observable: >-
      The `tester-unified` gate (a full pytest run, MUTATION-CHECKED: a stray `import
      coverage_gate`/`import mutation_gate`/`import gate_canary` left in any touched file
      reintroduces the deleted module at collection time and fails the run) passes green
      on HEAD.
    negative: >-
      A gate run that never actually collects/executes the touched test files (e.g. a
      selection filter that skips them) does not satisfy this oracle -- the full suite must run.
    gate: tester-unified
  - id: O3
    observable: >-
      `python -m nyxloom.cli gate --help` (or the equivalent installed entry point) lists no
      `verify` subcommand under `gate`.
    negative: >-
      A `verify` subcommand still present, even if it prints a deprecation notice instead of
      running the probe, fails this oracle -- GA1 is removed, not stubbed.
    gate: tester-unified
  - id: O4
    observable: >-
      `python -c "import json; d=json.load(open('src/nyxloom/schemas/nyxloom-config.schema.json'));
      print('assay-verdict' in d['properties']['gates']['additionalProperties']['properties']['asserts']['items']['enum'])"`
      prints `True` (adjust the JSON-path expression to the enum's real nesting if it differs --
      the schema at src/nyxloom/schemas/nyxloom-config.schema.json:127-130 today lists
      tests-pass/changed-line-coverage/mutation/canary-verified at that nesting).
    negative: >-
      "assay-verdict" appearing anywhere else in the file (a comment, an unrelated enum) does
      not satisfy this oracle -- it must be a member of THIS asserts enum array.
    gate: tester-unified
  - id: O5
    observable: >-
      `grep -c "OFFERED, not mandated -- the toolkit" nyxloom/reference/STANDARD.md` and
      `grep -c "nyxloom gate verify" nyxloom/reference/STANDARD.md` both print `0`.
    negative: >-
      A reworded but still-present toolkit-offer paragraph (i.e. `coverage_gate.py`/
      `mutation_gate.py` still described as an available opt-in tool anywhere in STANDARD.md)
      fails this oracle.
    gate: tester-unified
  - id: O6
    observable: >-
      nyxloom-trove/decisions.md contains a new entry dated 2026-09-02 or later whose text
      names both: (a) reversing the 2026-07-27 mutation_gate enablement directive's premise
      that the toolkit modules earn their keep, and (b) retiring GA1/GA4 (gate_canary-based
      external gate-trustworthiness verification) as superseded by Assay's own R2/R3
      mechanisms once a project declares assay/run-gate lanes, citing
      nyxloom-trove/reports/ASSAY-NYXLOOM-REORIENTATION-2026-08-17.md's "Deletion inventory
      and Assay transfer check" section as the prior analysis this executes.
    negative: >-
      A decisions.md entry that only says "removed dead code" without naming the reversed
      directive and the superseding mechanism does not satisfy this oracle.
    gate: tester-unified
  - id: O7
    observable: >-
      nyxloom-trove/handoffs/nyxloom-P90-extract-testing-library.md no longer exists at that
      path; nyxloom-trove/archive/nyxloom-P90-extract-testing-library.md exists and its first
      10 lines contain the word "superseded".
    negative: >-
      Deleting P90 outright (no archive copy) or archiving it unchanged (no superseded note)
      both fail this oracle.
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "any touched non-test file outside this list needs an edit to keep the gate green (a
    reverse-dependency this carve's sweep missed)"
  - "coverage_gate.py, mutation_gate.py, or gate_canary.py has a real external importer
    outside nyxloom/ (this carve's sweep found none anywhere in vbpub as of 286a4bc0 --
    re-run `git grep -rln \"from nyxloom.*import.*\\(coverage_gate\\|mutation_gate\\|gate_canary\\)\"`
    across the repo root before trusting that finding is still current)"
  - "effects_merge.py's phase='mutation' wiring or config.py's GateDef.mutation_gate field
    would need to change to keep the gate green -- scope.forbid says they should not, so a
    contradiction here is a carve defect, not implementer discretion"
  - "E-008 checkpoint clause: arm at ~120k context or ~60 tool calls, cut at the next
    coherent boundary (green gate > commit > LOG/REPORT write), repeat every ~40-55 calls,
    stop with <~40 calls remaining. At the cut, write a continuation brief to
    nyxloom-trove/reports/nyxloom-P98-BRIEF.md and a self-authored retention prompt to
    nyxloom-trove/reports/nyxloom-P98-COMPACT.md (both authorised touches), commit, and
    return -- do not resume/fork past the cut yourself."
---

# nyxloom-P98 — retire the coverage/mutation/canary toolkit + GA1/GA4 gate-verify feature

## BLOCKED protocol

If any contract item below cannot be met exactly as specified, or an
`escalate_if` condition fires, stop and report **BLOCKED: <reason>** rather
than improvising a substitute. Do not silently narrow, widen, or reinterpret
a contract item.

## Context to read first

1. `nyxloom/nyxloom-trove/reports/ASSAY-NYXLOOM-REORIENTATION-2026-08-17.md`
   — full document. Its "Deletion inventory and Assay transfer check" section
   is the prior analysis this package executes; its endorsement of deleting
   `gate_canary.py` (Assay's own R3 canary mechanism is "present and
   stronger") is why O6 requires citing it rather than re-deriving the
   argument.
2. `nyxloom/reference/STANDARD.md` §"What nyxloom requires of a project (the
   gate contract)" — the "OFFERED, not mandated — the toolkit" paragraph and
   the `nyxloom gate verify`/GA1 paragraph immediately after it, both being
   retired.
3. `nyxloom/assay.toml` header comment — the stale claim being corrected.
4. `nyxloom/src/nyxloom/effects_merge.py` lines ~128-145 — read this BEFORE
   touching anything named "mutation": confirms the `("mutation", ...,
   effects_gates.select_mutation_gate)` post-merge wiring is generic
   (selects a project-*declared* `phase='mutation'` `GateDef`, never imports
   `mutation_gate.py`) and must not be touched. `git grep -n "coverage_gate\|
   gate_canary" -- src/nyxloom/effects_merge.py` returns nothing; its only
   "mutation_gate" hit is the `cfg.policy.mutation_gate` boolean read.
5. `nyxloom/src/nyxloom/reconcile.py` lines ~280-340 and ~570-585 — the
   `VerifyGate` action and module-contract item 16 (GATE VERIFY CADENCE)
   being removed; read the surrounding numbered items so the removal doesn't
   disturb items 15/17's ordering commentary, which cross-references item 16
   by number.
6. `nyxloom/src/nyxloom/effects_gates.py` lines ~120-230 and ~450-465 — the
   `GateEffector.verify_gate`/`_run_verify_probe`/`drain_verify` methods and
   their effect-registration entry being removed.
7. `nyxloom/nyxloom-trove/handoffs/nyxloom-P90-extract-testing-library.md` —
   read in full before archiving it; its own body is what makes it
   superseded (it would recreate what Assay now provides).

## Work

1. **Delete the toolkit modules and their dedicated tests.** Remove
   `src/nyxloom/coverage_gate.py`, `src/nyxloom/mutation_gate.py`,
   `src/nyxloom/gate_canary.py`, `tests/test_coverage_gate.py`,
   `tests/test_mutation_gate.py`, `tests/test_gate_canary.py`,
   `tests/test_gate_verify_cadence.py`.
2. **Remove the GA1 CLI surface.** Delete `cmd_gate_verify` from `cli.py`,
   its `gate_verify_parser` argparse registration and the `args.gate_cmd ==
   "verify"` dispatch branch, and the `gate verify <project>` entry in the
   CLI's own help text block.
3. **Remove the GA4 daemon cadence end to end.** In `effects_gates.py`:
   delete the `gate_canary` import, `GateEffector.verify_gate`,
   `_run_verify_probe`, `drain_verify`, the `verify_running`/`verify_results`
   state in `__init__`, and the effect-registration entry that wires
   `emits={EventType.GATE_VERIFY_RECORDED, ...}` / `drain=effector.
   drain_verify`. In `daemon.py`: remove `gate_canary` from the
   `effects_gates` import list, `_days_since_gate_verify`, and its call site
   feeding `ReconcileInput`. In `reconcile.py`: delete the `VerifyGate`
   `Action` dataclass, module-contract item 16's scheduling condition and
   its docstring paragraph (renumber neighboring cross-references to item 16
   if any refer to it by number — check items 15 and 17's prose), and the
   `days_since_gate_verify` field + comment on `ReconcileInput`. In
   `rules_attention.py`: delete the gate-verify-cadence-overdue attention
   rule. In `types.py`: delete `EventType.GATE_VERIFY_RECORDED`. In
   `config.py`: delete `GateDef.gate_verify_interval_days` and its comment.
4. **Update the test files that reference the removed GA1/GA4 surface**
   (`test_cli.py`, `test_daemon.py`, `test_effects.py`, `test_invariants.py`,
   `test_planning.py`, `effect_differential.py`, `legacy_planner.py`):
   remove every test/fixture referencing `VerifyGate`, `GATE_VERIFY_RECORDED`,
   `drain_verify`, `_run_verify_probe`, `cmd_gate_verify`, `gate_canary`, or
   `coverage_gate`. **Do not touch** the six `test_mutation_gate_*` functions
   in `test_daemon.py` (~lines 6136-6390: `test_mutation_gate_pass_merges`,
   `test_mutation_gate_failure_rejects`, `test_mutation_gate_disabled_skips`,
   `test_mutation_gate_no_declared_gate_skips`, `test_mutation_gate_timeout_expired`,
   `test_mutation_gate_oserror`) — they exercise `effects_merge.py`'s kept
   generic wiring via a plain `argv=['true']`/`argv=['false']` `GateDef`,
   never the deleted module.
5. **Fix the two remaining toolkit references that would otherwise dangle.**
   In `onboarding_gate.py`, remove the "ecosystem `coverage_gate.py`" clause
   and the "run `nyxloom gate verify <project>`" sentence from the
   missing-gate guidance text (keep the `cargo llvm-cov`/`nyc` examples —
   those are real external tools, unaffected). In `gate_scaffold.py`, drop
   the `"python -m nyxloom.coverage_gate --base main --coverage-json ... "`
   line from the scaffolded `inner` command (leave the plain pytest+coverage-
   JSON invocation, without the nyxloom-side judging step), change
   `asserts=["tests-pass", "changed-line-coverage"]` to
   `asserts=["tests-pass"]` (the scaffold no longer measures a coverage
   floor itself), and replace the `ADJUST_MARKER` trailing comment's
   coverage-floor advice with a pointer to adopting `run-gate`+`assay`
   (`run-gate-project/CONSUMERS.md`, `assay/docs/CONSUMERS.md`) for real
   rigor.
6. **NL-4 — add `"assay-verdict"` to the asserts schema enum.** In
   `src/nyxloom/schemas/nyxloom-config.schema.json`, add `"assay-verdict"` to
   the `asserts` array's `enum` (alongside the existing `tests-pass`,
   `changed-line-coverage`, `mutation`, `canary-verified`). Mirror the
   addition in `config.py`'s `GateDef` docstring comment listing the same
   enum, and in `STANDARD.md`'s prose list (`asserts=[tests-pass|
   changed-line-coverage|mutation|canary-verified]` becomes `...|
   canary-verified|assay-verdict]`).
7. **Update the two stale doc/comment claims.** In `STANDARD.md`, delete the
   "**OFFERED, not mandated — the toolkit.**" paragraph in full (it describes
   the now-deleted modules as an available opt-in), and delete the
   `nyxloom gate verify`/GA1 paragraph that follows the asserts-enum
   sentence (both in the "What nyxloom requires of a project" section). In
   `nyxloom/assay.toml`'s header comment, replace the sentence claiming
   `mutation_gate.py` "still imports its helpers, and it remains the toolkit
   nyxloom OFFERS other projects per STANDARD.md" with a note that the
   module has been deleted (nyxloom-P98) and Assay's own R2/R3 mechanisms
   are the toolkit now.
8. **Record the decision.** Add a dated entry to `nyxloom-trove/decisions.md`
   stating: the toolkit modules (`coverage_gate.py`/`mutation_gate.py`/
   `gate_canary.py`) are deleted because no project (including nyxloom
   itself) ever imported them as a library, and GA1/GA4's external
   gate-trustworthiness verification is retired because Assay's own R2/R3
   mechanisms supersede it once a project declares assay/run-gate lanes
   (citing the 2026-08-17 reorientation report's deletion-inventory
   analysis as the prior work this executes). Explicitly name that this
   reverses the 2026-07-27 operator directive that enabled `mutation_gate`
   in `nyxloom-trove/nyxloom.toml` on the premise the toolkit was worth
   dogfooding — record why that premise no longer holds (nyxloom's own
   `[gates.tester-unified]` already runs entirely through `run-gate.py`
   per nyxloom-P48, and never declared a `phase='mutation'` gate that
   would have exercised the toolkit anyway).
9. **Close the superseded P90 handoff.** Move
   `nyxloom-trove/handoffs/nyxloom-P90-extract-testing-library.md` to
   `nyxloom-trove/archive/nyxloom-P90-extract-testing-library.md`, prepending
   a short note at the top: superseded by the 2026-08-17 reorientation
   analysis and closed by nyxloom-P98 (it would have recreated what Assay
   now provides).

## Scope / forbid

Out of scope — do not touch, even though the names look related:

- **`src/nyxloom/gate_runner.py`** — the generic gate-argv executor
  (`select_verification_gate`, `run_gate_at_commit`). It imports neither
  `coverage_gate` nor `mutation_gate` nor `gate_canary` today and becomes the
  *only* gate-execution path once this package lands. No change needed.
- **`src/nyxloom/effects_merge.py`**, specifically the `("mutation",
  getattr(cfg.policy, "mutation_gate", False), effects_gates.select_mutation_gate)`
  post-merge wiring — this is generic: it selects whatever `GateDef` a
  project *declares* with `phase='mutation'` and runs its `argv` verbatim,
  the same as the neighboring `"pre-merge"` entry. It has never imported the
  deleted `mutation_gate.py` module. Confirmed by sweep: `git grep -n
  "coverage_gate\|gate_canary" -- src/nyxloom/effects_merge.py` returns
  nothing; the file's only "mutation_gate" hit is the `cfg.policy.
  mutation_gate` boolean attribute read, which is the kept opt-in toggle,
  not a module reference.
- **`src/nyxloom/config.py`'s `GateDef.mutation_gate: bool` field** (the F017
  opt-in toggle consumed by the wiring above) — stays. Only
  `GateDef.gate_verify_interval_days` is removed from this file (Work item
  3); `mutation_gate` is a different field and is not part of this package.
- **`tests/test_daemon.py`'s six `test_mutation_gate_*` functions**
  (~lines 6136-6390) — these exercise the kept `effects_merge.py` wiring via
  a bare `argv=['true']`/`argv=['false']` `GateDef`, not the deleted module.
  Leave them exactly as they are.
- **`nyxloom-trove/nyxloom.toml`** — its `[gates.tester-unified]` already
  runs entirely through `./run-gate.py` (nyxloom-P48); it declares no
  `phase='mutation'` gate today, so nothing in this file references a
  deleted module. No edit needed.

## Environment setup

Mode-B, this worktree only (`/workspaces/vbpub/.worktrees/nyxloom-p98`,
branch `feat/nyxloom-P98-retire-toolkit-gate-verify`). No package image tag
is needed — this package touches no Dockerfile/scaffolded-image path; the
gate runs via `./run-gate.py --worktree {worktree} tester-unified` exactly
as nyxloom's own `[gates.tester-unified]` already declares. Both-prefix
teardown per GUIDE §3.3 applies if any container gets started for local
iteration.

## Gate argv (verbatim)

```
cd /workspaces/vbpub/.worktrees/nyxloom-p98/nyxloom && ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-p98 tester-unified
```
