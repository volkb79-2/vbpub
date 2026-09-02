---
schema_version: 1
id: nyxloom-P98-retire-toolkit-gate-verify
project: nyxloom
title: "Retire the coverage/mutation/canary toolkit + GA1/GA4 gate-verify feature"
tier: implement-2
input_revision: "5473063c"
depends_on: []
session: fresh
source:
  kind: roadmap
  ref: nyxloom-trove/reports/ASSAY-NYXLOOM-REORIENTATION-2026-08-17.md
scope:
  touch:
    - "src/nyxloom/mutants.py"                     # NEW: Mutant dataclass + generate_mutants, extracted from mutation_gate.py (the only pieces tools/remote_mutation_audit.py needs; verified no downward dependency on the deleted gate-judgment half)
    - "src/nyxloom/coverage_gate.py"                # delete
    - "src/nyxloom/mutation_gate.py"                # delete (after extracting Mutant/generate_mutants to mutants.py)
    - "src/nyxloom/gate_canary.py"                  # delete
    - "tests/test_coverage_gate.py"                 # delete
    - "tests/test_mutation_gate.py"                 # delete -- but see Work item 1: if any of its cases test generate_mutants itself (not evaluate/CLI), move those cases to a new tests/test_mutants.py instead of deleting them
    - "tests/test_gate_canary.py"                   # delete
    - "tests/test_gate_verify_cadence.py"           # delete
    - "tools/remote_mutation_audit.py"              # change `from nyxloom.mutation_gate import Mutant, generate_mutants` to `from nyxloom.mutants import Mutant, generate_mutants` -- the only edit this file needs
    - "src/nyxloom/cli.py"                          # remove cmd_gate_verify + its argparse wiring + help text
    - "src/nyxloom/effects_gates.py"                # remove gate_canary import; verify_gate/_run_verify_probe/drain_verify methods; verify_running/verify_results state; the GATE_VERIFY_RECORDED effect registration entry
    - "src/nyxloom/daemon.py"                       # remove gate_canary from the effects_gates-adjacent import list; remove _days_since_gate_verify + its call site feeding ReconcileInput
    - "src/nyxloom/reconcile.py"                    # remove VerifyGate Action class; remove module-contract item 16 (scheduling condition + docstring paragraph); KEEP ReconcileInput.days_since_gate_verify declared (see Scope/forbid -- tests/legacy_planner.py's frozen snapshot reads it unconditionally); see Work item 3 for the item-17 cross-reference and the renumbering decision
    - "src/nyxloom/planning.py"                     # remove the RuleSpec(name="gate-verify", rule=rules_attention.gate_verify, emits=frozenset({"VerifyGate"}), channel=Channel.GATE_VERIFY) entry from rule_table() (~line 1218-1224) -- required, or rule_table()/plan_project raises AttributeError the moment rules_attention.gate_verify is deleted
    - "src/nyxloom/rules_attention.py"              # remove the gate-verify-cadence-overdue attention rule (the gate_verify function planning.py calls)
    - "src/nyxloom/types.py"                        # remove EventType.GATE_VERIFY_RECORDED
    - "src/nyxloom/config.py"                       # KEEP Policy.gate_verify_interval_days declared (see Scope/forbid -- tests/legacy_planner.py's frozen snapshot reads it unconditionally via the same Policy instance fed to the live planner); ADD "assay-verdict" to the asserts-enum docstring comment on GateDef.asserts (config.py:64-65, NL-4); remove the stale cli.cmd_gate_verify sentence from that same docstring (config.py:66-70)
    - "src/nyxloom/onboarding_gate.py"              # TWO separate mentions to fix (Work item 5): the missing-gate guidance text (~line 45-56) AND _has_gate_recommendation's own text (~line 60-69)
    - "src/nyxloom/gate_scaffold.py"                # drop the `python -m nyxloom.coverage_gate` line from the scaffolded argv; asserts=["tests-pass"] (drop changed-line-coverage, no longer measured); point the ADJUST_MARKER comment at run-gate/assay adoption instead
    - "src/nyxloom/schemas/nyxloom-config.schema.json"  # NL-4: add "assay-verdict" to the asserts enum array (properties.gates.additionalProperties.properties.asserts.items.enum, lines 127-130)
    - "reference/STANDARD.md"                       # THREE separate edits, sequenced -- see Work item 7 for the exact split (do not delete a whole paragraph; the asserts-enum sentence inside it survives, edited for NL-4)
    - "assay.toml"                                   # update the header comment: it currently claims mutation_gate.py "remains the toolkit nyxloom OFFERS other projects" -- false once deleted
    - "nyxloom-trove/decisions.md"                   # new dated decision record (see Work item 8)
    - "nyxloom-trove/reports/ASSAY-NYXLOOM-REORIENTATION-2026-08-17.md"  # already imported+annotated by the carver; read-only for the implementer
    - "nyxloom-trove/handoffs/nyxloom-P90-extract-testing-library.md"    # move to nyxloom-trove/archive/, prepend a superseded note
    - "nyxloom-trove/archive/nyxloom-P90-extract-testing-library.md"     # the move destination
    - "tests/test_cli.py"                           # remove/adjust references to cmd_gate_verify / `gate verify`
    - "tests/test_daemon.py"                        # remove tests asserting VerifyGate/_run_verify_probe/GATE_VERIFY_RECORDED/gate_canary behavior (the fields themselves stay valid, see Scope/forbid -- only remove tests that assert the deleted scheduling/probe behavior); KEEP the six test_mutation_gate_* functions (~lines 6136-6390) unchanged -- they test effects_merge.py's generic phase='mutation' re-run wiring, which this package does NOT touch or delete
    - "tests/test_effects.py"                       # remove references to VerifyGate/GATE_VERIFY_RECORDED/drain_verify
    - "tests/test_invariants.py"                    # remove references to VerifyGate/GATE_VERIFY_RECORDED
    - "tests/test_planning.py"                      # remove tests asserting VerifyGate scheduling behavior AND the gate-verify RuleSpec/rule_table assertions tied to planning.py's removed entry (the two fields themselves stay valid, see Scope/forbid)
    - "tests/effect_differential.py"                # remove references to VerifyGate/GATE_VERIFY_RECORDED/drain_verify
    - "tests/corpus_profiles.py"                    # the "gate-verify-due" profile tuple (~lines 192-194, tagged "contract item 16") sets gate_verify_interval_days/days_since_gate_verify -- delete this profile entry entirely (the cadence it exercises no longer exists; both fields stay valid ReconcileInput/Policy kwargs regardless, see Scope/forbid, so this is a scenario-relevance deletion, not a compile-fix)
    - "tests/test_gap_audit.py"                     # verify-only, no edit expected: its `_inp` helper (~line 82) sets days_since_gate_verify=100.0 as one of many unrelated ReconcileInput kwargs -- valid and unaffected since the field stays declared (see Scope/forbid); listed because O2 requires confirming it still collects and passes
    - "tests/planner_corpus.py"                     # consumes corpus_profiles.PROFILES generically -- verify it still runs green after the profile entry above is removed; no edit expected unless it special-cases that profile by name
    - "tests/test_planner_differential.py"          # same as planner_corpus.py -- consumes PROFILES generically, verify-only unless it special-cases "gate-verify-due" by name
    - "tests/test_snapshot_faults.py"                # module-level IRREVERSIBLE tuple includes EventType.GATE_VERIFY_RECORDED (~line 174) -- drop that member from the tuple, or it's an AttributeError at collection time
    - "tests/test_remote_mutation_audit_tools.py"    # verify-only, no edit expected: its `worker` fixture dynamically exec's tools/remote_mutation_audit.py rather than importing nyxloom.mutation_gate directly, so it needs no change once that file's import is updated (Work item 1); listed because O2 requires confirming it still collects and passes
    - "tests/test_core_characterization.py"          # verify-only, no edit expected: it reads the inventory doc below and asserts against the real tree -- fixing the doc (Work item 10) is what makes it pass again; listed because O2 requires confirming it collects and passes
    - "nyxloom-trove/reports/nyxloom-P98-REPORT.md"  # NEW: the O8 symbol-absence script's source + PASS output, appended here as proof
    - "nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md"  # a LIVE, mechanically-checked inventory (unlike legacy_planner.py -- this one is meant to track current reality; tests/test_core_characterization.py enforces it), missed by both prior review rounds because it's a nyxloom-trove/reports/ doc, outside the src/tests/tools sweep scope. THREE rows need fixing, not two (fix-verification round 3 found the third by recomputing the tolerance check over every row, not just the two the carve first named -- see Work item 10): remove the `src/nyxloom/gate_canary.py` row entirely (file deleted); re-measure and update `src/nyxloom/effects_gates.py`, `src/nyxloom/cli.py`, and `src/nyxloom/rules_attention.py`'s recorded line counts with real `wc -l` output (effects_gates.py and cli.py both already exceed their own declared tolerance today; do not hardcode a number); trim the "Present responsibility" text on the effects_gates.py and rules_attention.py rows, both of which describe the now-deleted gate-verify cadence. Follow the doc's own convention (see its "Re-measured DATE (CR-NN review) for ..." paragraphs) and add a short "Re-measured <today> (nyxloom-P98)" note explaining all three changes.
  forbid:
    - "src/nyxloom/gate_runner.py"       # generic gate-argv executor; stays, becomes the ONLY gate-execution path
    - "src/nyxloom/effects_merge.py"     # the ("mutation", getattr(cfg.policy, "mutation_gate", False), effects_gates.select_mutation_gate) wiring is GENERIC (picks a project-DECLARED phase='mutation' GateDef, never imports the deleted module) -- confirmed by reverse-dependency sweep and independently re-verified by adversarial review; do not touch
    - "tests/legacy_planner.py"          # ABSOLUTE: a mechanically self-verifying byte-identical copy of reconcile.py at commit 052857ae (its own header: "DO NOT EDIT THIS MODULE TO MAKE A TEST PASS"). tests/test_planner_differential.py::test_legacy_baseline_is_the_committed_branch_point asserts this file, after undoing its two declared import-only edits, is byte-identical to `git show 052857ae:...reconcile.py`. It reads Policy.gate_verify_interval_days and ReconcileInput.days_since_gate_verify UNCONDITIONALLY off the same production dataclass instances fed to the live planner -- this is WHY those two fields stay declared in config.py/reconcile.py (both touched for OTHER reasons -- see their scope.touch entries and the Scope/forbid section below) instead of being deleted. Editing this file to accommodate anything is itself the defect the byte-identity check exists to catch.
    - "nyxloom-trove/nyxloom.toml"        # its own [gates.tester-unified] argv already runs run-gate.py (P48); it declares no phase='mutation' gate today, so nothing here references a deleted module -- no edit needed
oracles:
  - id: O1
    observable: >-
      `git ls-files` from the repo root shows NONE of: src/nyxloom/coverage_gate.py,
      src/nyxloom/mutation_gate.py, src/nyxloom/gate_canary.py, tests/test_coverage_gate.py,
      tests/test_mutation_gate.py, tests/test_gate_canary.py, tests/test_gate_verify_cadence.py.
      It DOES show src/nyxloom/mutants.py.
    negative: >-
      Any of the seven deleted paths still tracked (including under a renamed/moved path,
      e.g. relocating mutation_gate.py's body into gate_runner.py under a new filename) is a
      failure -- this is a closed, named list, not a pattern sweep, and relocating deleted
      logic elsewhere does not satisfy it.
    gate: tester-unified
  - id: O2
    observable: >-
      The `tester-unified` gate (a full pytest run, MUTATION-CHECKED: a stray `import
      coverage_gate`/`import mutation_gate`/`import gate_canary` left in any touched file
      reintroduces a deleted module at collection time and fails the run) passes green
      on HEAD, AND collection includes tests/test_planning.py, tests/test_gap_audit.py,
      tests/test_daemon.py, tests/test_remote_mutation_audit_tools.py,
      tests/test_snapshot_faults.py, tests/test_planner_differential.py, and
      tests/test_core_characterization.py by name (not merely "the suite as a whole" -- a
      selection filter that skips any of these does not satisfy this oracle). The
      differential file specifically must include
      test_legacy_baseline_is_the_committed_branch_point and the full PROFILES/corpus
      parametrization; the characterization file specifically must include
      test_inventory_paths_all_exist and test_inventory_sizes_are_within_the_declared_tolerance
      (Work item 10 exists because a real gate run, not either review round, is what caught
      these two failing first).
    negative: >-
      A gate run that never actually collects/executes the seven named test files does not
      satisfy this oracle even if it exits 0.
    gate: tester-unified
  - id: O3
    observable: >-
      `python -m nyxloom.cli gate --help` (or the equivalent installed entry point) lists no
      subcommand under `gate` whose help text or implementation performs a canary-based
      trustworthiness probe (i.e. no subcommand importing gate_canary or reproducing its
      behavior under a different name).
    negative: >-
      A `verify` subcommand renamed to any other name (e.g. `gate audit`) while keeping the
      same canary-probing body fails this oracle -- GA1 is removed, not renamed.
    gate: tester-unified
  - id: O4
    observable: >-
      `python -c "import json; d=json.load(open('src/nyxloom/schemas/nyxloom-config.schema.json'));
      print('assay-verdict' in d['properties']['gates']['additionalProperties']['properties']['asserts']['items']['enum'])"`
      prints `True`. AND `grep -c "assay-verdict" src/nyxloom/config.py` and
      `grep -c "assay-verdict" reference/STANDARD.md` both print a value >= 1 (the two prose
      mirrors required by Work item 6).
    negative: >-
      "assay-verdict" present in the JSON schema alone, with the config.py docstring comment
      or STANDARD.md's prose enum list left unmirrored, does not satisfy this oracle.
    gate: tester-unified
  - id: O5
    observable: >-
      `grep -c "OFFERED, not mandated -- the toolkit" reference/STANDARD.md` prints `0`.
      `grep -c "nyxloom gate verify" reference/STANDARD.md` prints `0` (all three pre-existing
      occurrences -- the TRANSPORT_UNTRUSTED bullet, the gate-rigor paragraph, and validation
      item 7 -- are gone or reworded per Work item 7's exact instructions). AND
      `grep -c "asserts=\[tests-pass" reference/STANDARD.md` still prints >= 1 (the rigor-
      declaration prose survives, edited for NL-4, per Work item 7's explicit split).
    negative: >-
      Deleting the entire "Gate rigor is a first-class, per-project fact" paragraph (which
      would satisfy the first two greps at the cost of losing the asserts-enum prose sentence
      NL-4 needs preserved) fails the third check and does not satisfy this oracle.
    gate: tester-unified
  - id: O6
    observable: >-
      nyxloom-trove/decisions.md contains a new entry dated 2026-09-02 or later of at least
      150 words whose text names both: (a) reversing the 2026-07-27 mutation_gate enablement
      directive's premise that the toolkit modules earn their keep, explaining that nyxloom's
      own [gates.tester-unified] already runs entirely through run-gate.py (nyxloom-P48) and
      never declared a phase='mutation' gate that would have exercised the toolkit, and (b)
      retiring GA1/GA4 (gate_canary-based external gate-trustworthiness verification) as
      superseded by Assay's own R2/R3 mechanisms once a project declares assay/run-gate lanes,
      citing nyxloom-trove/reports/ASSAY-NYXLOOM-REORIENTATION-2026-08-17.md's "Deletion
      inventory and Assay transfer check" section as the prior analysis this executes.
    negative: >-
      A keyword-stuffed one-line entry that names both facts without explaining the reasoning
      behind either (i.e. does not read as a decision record someone could act on without
      re-deriving the argument) does not satisfy this oracle -- the 150-word floor is a proxy
      for "actually reasoned," not a target to pad.
    gate: tester-unified
  - id: O7
    observable: >-
      nyxloom-trove/handoffs/nyxloom-P90-extract-testing-library.md no longer exists at that
      path; nyxloom-trove/archive/nyxloom-P90-extract-testing-library.md exists, and its first
      15 lines contain a note, in prose (not a bare HTML comment or tag), that (a) names
      nyxloom-P98 as the closing package and (b) states the specific reason: it would recreate
      testing capability Assay now provides.
    negative: >-
      A content-free marker (e.g. a bare `<!-- superseded -->` line, or the word "superseded"
      appearing without stating why) fails this oracle even though it contains the word.
    gate: tester-unified
  - id: O8
    observable: >-
      A script that imports each touched module and asserts, via `hasattr`/`dataclasses.
      fields`/introspection (not string grep, so relocation-under-a-new-name does not fool
      it): `reconcile.VerifyGate` does not exist as an attribute of the `reconcile` module;
      `effects_gates.GateEffector` has no `verify_gate`, `_run_verify_probe`, or `drain_verify`
      attribute; `types.EventType` has no `GATE_VERIFY_RECORDED` member; `rules_attention`
      module has no `gate_verify` attribute; no entry in `planning.rule_table()`'s returned
      specs has a `rule` attribute equal to `rules_attention.gate_verify` (moot once that
      attribute is gone, but checked directly: calling `planning.rule_table()` does not
      raise, and none of its specs reference a gate-verify-cadence channel); AND, the
      OPPOSITE checks for the two fields tests/legacy_planner.py depends on (see
      Scope/forbid): `[f.name for f in dataclasses.fields(config.Policy)]` STILL contains
      `gate_verify_interval_days`, and `[f.name for f in dataclasses.fields(reconcile.
      ReconcileInput)]` STILL contains `days_since_gate_verify` — deleting either is a
      regression against tests/legacy_planner.py's frozen byte-identity check, not a more
      thorough retirement. This script's full source and its PASS output are appended to
      nyxloom-trove/reports/nyxloom-P98-REPORT.md.
    negative: >-
      Leaving any of the removed symbols in place but unreachable from their normal call path
      (e.g. `verify_gate` renamed to `_verify_gate_unused` and never called) fails this oracle
      -- the check is symbol *existence*, deliberately independent of whether anything still
      calls it. Symmetrically, deleting `Policy.gate_verify_interval_days` or
      `ReconcileInput.days_since_gate_verify` also fails this oracle, even though every other
      check passes -- they are the one deliberate exception, kept for
      tests/legacy_planner.py, not a missed cleanup.
    gate: tester-unified
  - id: O9
    observable: >-
      `grep -n "coverage_gate" src/nyxloom/onboarding_gate.py src/nyxloom/gate_scaffold.py`
      returns no matches in either file, `grep -c "nyxloom gate verify"
      src/nyxloom/onboarding_gate.py` prints `0` (both of onboarding_gate.py's two mentions,
      per Work item 5, are gone), and `grep -c "cmd_gate_verify" src/nyxloom/config.py`
      prints `0` (the stale GateDef.asserts docstring reference, Work item 6).
    negative: >-
      Fixing only one of onboarding_gate.py's two mentions (the missing-gate guidance text OR
      _has_gate_recommendation, but not both) fails this oracle. So does leaving config.py's
      docstring reference to the deleted cmd_gate_verify in place.
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "any touched non-test file outside this list needs an edit to keep the gate green (a
    reverse-dependency this carve's sweep missed) -- this carve was adversarially reviewed
    once already (nyxloom-trove/reports/nyxloom-P98-CARVE-REVIEW.md) and repaired against
    every finding; a NEW miss surfacing at dispatch means the sweep needs a third pass, not
    implementer improvisation"
  - "coverage_gate.py, the deleted 90% of mutation_gate.py (anything but Mutant/
    generate_mutants), or gate_canary.py has a real importer beyond the ones named in
    scope.touch (re-run `git grep -rln \"from nyxloom.*import.*\\(coverage_gate\\|mutation_gate\\|
    gate_canary\\)\"` across the whole repo root, and separately `git grep -rln
    \"nyxloom\\.\\(coverage_gate\\|mutation_gate\\|gate_canary\\)\"`, before trusting this
    carve's finding is still current)"
  - "extracting Mutant/generate_mutants into mutants.py requires touching anything in
    mutation_gate.py's gate-judgment half (evaluate, _fanout_safe, _run_is_killed*,
    _resolve_added_lines) -- verified clean (generate_mutants has zero calls into that half)
    at input_revision; re-verify this before extracting, since a dependency here means the
    split this handoff specifies does not hold"
  - "effects_merge.py's phase='mutation' wiring or config.py's Policy.mutation_gate field
    would need to change to keep the gate green -- scope.forbid says they should not, so a
    contradiction here is a carve defect, not implementer discretion"
  - "reconcile.py's module-contract item 17 needs renumbering to close the gap left by
    removing item 16 -- this handoff deliberately leaves a numbering gap (see Work item 3)
    rather than cascading a renumber into docs/plan-gap-engine-and-reviewer-repair.md,
    docs/plan-next-batches.md, or the 'WHERE EACH ITEM LIVES' table, none of which are in
    scope; if leaving the gap breaks a test that asserts contiguous item numbers, that is a
    genuine BLOCKED, not something to paper over by renumbering out-of-scope files"
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

1. `nyxloom-trove/reports/ASSAY-NYXLOOM-REORIENTATION-2026-08-17.md` — full
   document. Its "Deletion inventory and Assay transfer check" section is
   the prior analysis this package executes; its endorsement of deleting
   `gate_canary.py` (Assay's own R3 canary mechanism is "present and
   stronger") is why O6 requires citing it rather than re-deriving the
   argument.
2. `nyxloom-trove/reports/nyxloom-P98-CARVE-REVIEW.md` — the adversarial
   review this repair responds to. Read it in full; every scope.touch entry
   and oracle added since the first freeze traces to a specific finding in
   it. Do not re-open a finding it already settled.
3. `reference/STANDARD.md` §"What nyxloom requires of a project (the gate
   contract)" — lines ~192, ~199-225, and §"Validation methodology" item 7 —
   the three occurrences of "nyxloom gate verify" and the "OFFERED, not
   mandated" paragraph, both being edited per Work item 7's exact split.
4. `assay.toml` header comment — the stale claim being corrected.
5. `src/nyxloom/mutation_gate.py` lines 73-276 (the `Mutant` dataclass and
   `generate_mutants`) vs. lines 277-689 (`MutationResult`, `evaluate`,
   `_fanout_safe`, `_run_is_killed*`, `_resolve_added_lines`,
   `_build_arg_parser`, `_derive_test_command`, `main`) — the extraction
   boundary for Work item 1. `generate_mutants` calls nothing below line
   277; `MutationResult` is used only by `evaluate` (deleted).
6. `tools/remote_mutation_audit.py` line 33 — its real, load-bearing import
   of `Mutant`/`generate_mutants` (mutant-job construction at lines
   179-206), the reason `mutation_gate.py` cannot simply be deleted whole.
7. `src/nyxloom/effects_merge.py` lines ~128-145 — read this BEFORE
   touching anything named "mutation": confirms the `("mutation", ...,
   effects_gates.select_mutation_gate)` post-merge wiring is generic
   (selects a project-*declared* `phase='mutation'` `GateDef`, never
   imports `mutation_gate.py`) and must not be touched.
8. `src/nyxloom/planning.py` — the `RuleSpec(name="gate-verify", ...,
   rule=rules_attention.gate_verify, emits=frozenset({"VerifyGate"}),
   channel=Channel.GATE_VERIFY)` entry in `rule_table()` (~lines
   1218-1224). Missed in the first carve freeze; deleting
   `rules_attention.gate_verify` without removing this entry breaks
   `rule_table()`/`plan_project` for nearly every daemon/planner test.
9. `src/nyxloom/reconcile.py` lines ~280-340 and ~570-585 — the
   `VerifyGate` action and module-contract item 16 (GATE VERIFY CADENCE)
   being removed; item 17's docstring (`reconcile.py:335`) cross-references
   it **by field name** ("UNLIKE `test_health_interval_days` and
   `gate_verify_interval_days`, this does NOT fire..."), not by item
   number — read it before editing item 16's text.
10. `src/nyxloom/effects_gates.py` lines ~120-230 and ~450-465 — the
    `GateEffector.verify_gate`/`_run_verify_probe`/`drain_verify` methods
    and their effect-registration entry being removed.
11. `tests/corpus_profiles.py` lines ~192-194 (the `"gate-verify-due"`
    profile tuple, tagged "contract item 16") and its consumers
    `tests/planner_corpus.py`, `tests/test_planner_differential.py`.
12. `nyxloom-trove/handoffs/nyxloom-P90-extract-testing-library.md` — read
    in full before archiving it; its own body is what makes it superseded.
13. `tests/legacy_planner.py` lines 1-16 (its header) and
    `tests/test_planner_differential.py`'s
    `test_legacy_baseline_is_the_committed_branch_point` — read this BEFORE
    touching `Policy.gate_verify_interval_days` or `ReconcileInput.
    days_since_gate_verify`. `legacy_planner.py` is a mechanically
    self-verified byte-identical copy of `reconcile.py` at commit
    `052857ae`, read via `git show` specifically so no hand-edit can
    silently weaken it, and it reads both fields unconditionally off the
    SAME production `Policy`/`ReconcileInput` instances the live planner
    consumes. This is why Work item 3 keeps both fields declared rather
    than deleting them — a finding a prior dispatch attempt on this same
    package surfaced (correctly reporting BLOCKED rather than improvising)
    that neither adversarial review round caught, because the file was
    already correctly named in `scope.touch` for an unrelated reason and
    no review checked the file's own editability constraints, only its
    presence. Separately verified: the real historical corpus
    (`tests/fixtures/planner_corpus_v1.json`) contains zero entries
    referencing either field, so no `KNOWN_DIVERGENCES` entry in
    `test_planner_differential.py` is needed — the only scenario that ever
    exercised this cadence is the synthetic `"gate-verify-due"` profile
    Work item 4 deletes outright.

## Work

1. **Extract the mutation-generation engine, then delete the toolkit.**
   Create `src/nyxloom/mutants.py` containing the `Mutant` dataclass and
   `generate_mutants` function moved verbatim from `mutation_gate.py`
   (lines 73-80 and 96-276; re-verify first that `generate_mutants` still
   calls nothing below line 277 in the current tree — Context item 5).
   Update `tools/remote_mutation_audit.py`'s only relevant line: `from
   nyxloom.mutation_gate import Mutant, generate_mutants` becomes `from
   nyxloom.mutants import Mutant, generate_mutants`. If any case in
   `tests/test_mutation_gate.py` tests `generate_mutants` itself (as
   opposed to `evaluate`/the CLI), move that case to a new
   `tests/test_mutants.py`; delete the rest of `test_mutation_gate.py`.
   Then delete `src/nyxloom/coverage_gate.py`, the remainder of
   `src/nyxloom/mutation_gate.py`, `src/nyxloom/gate_canary.py`,
   `tests/test_coverage_gate.py`, `tests/test_gate_canary.py`, and
   `tests/test_gate_verify_cadence.py`.
2. **Remove the GA1 CLI surface.** Delete `cmd_gate_verify` from `cli.py`,
   its `gate_verify_parser` argparse registration and the `args.gate_cmd ==
   "verify"` dispatch branch, and the `gate verify <project>` entry in the
   CLI's own help text block.
3. **Remove the GA4 daemon cadence end to end, including its planner
   entry point.** In `effects_gates.py`: delete the `gate_canary` import,
   `GateEffector.verify_gate`, `_run_verify_probe`, `drain_verify`, the
   `verify_running`/`verify_results` state in `__init__`, and the
   effect-registration entry wiring `emits={EventType.
   GATE_VERIFY_RECORDED, ...}` / `drain=effector.drain_verify`. In
   `daemon.py`: remove `gate_canary` from the import list, and
   `_days_since_gate_verify` + its call site feeding `ReconcileInput` (the
   daemon simply stops computing/passing a value; the field itself stays
   declared on `ReconcileInput` — see below). In `reconcile.py`: delete the
   `VerifyGate` `Action` dataclass and module-contract item 16's scheduling
   condition and docstring paragraph. **Keep `ReconcileInput.
   days_since_gate_verify` declared, unedited** — do not delete this field
   (Context item 13: `tests/legacy_planner.py`'s frozen, byte-verified
   snapshot reads it unconditionally off the same production
   `ReconcileInput` instance the live planner consumes; deleting it breaks
   that file's mechanical self-check, which is itself forbidden to edit).
   Once item 16's scheduling condition is gone, the field is simply never
   read by the live planner again — that is the actual retirement; the
   field's bare existence is not. **Decision (do not deviate): leave a
   numbering gap where item 16 was** — do not renumber
   item 17 to 16. Item 17's own docstring cross-references item 16 by
   field name ("UNLIKE `test_health_interval_days` and
   `gate_verify_interval_days`..."); edit that sentence to drop the
   `gate_verify_interval_days` half of the comparison, but item 17 keeps
   its own number. Renumbering would cascade into
   `docs/plan-gap-engine-and-reviewer-repair.md`, `docs/plan-next-
   batches.md`, and the "WHERE EACH ITEM LIVES" table, none of which are
   in this package's scope. In `rules_attention.py`: delete the
   gate-verify-cadence-overdue attention rule (the `gate_verify` function
   `planning.py` calls). In `planning.py`: delete the `RuleSpec(name=
   "gate-verify", ...)` entry from `rule_table()` — this is required, not
   optional; leaving it after deleting `rules_attention.gate_verify`
   raises `AttributeError` the first time `rule_table()` runs. In
   `types.py`: delete `EventType.GATE_VERIFY_RECORDED`. **Keep `config.py`'s
   `Policy.gate_verify_interval_days` declared, unedited, for the same
   `tests/legacy_planner.py` reason** — this field lives on `class Policy`
   (lines 112-265), **not** `GateDef` (lines 56-71); do not touch `Policy.
   mutation_gate` either, a different field on the same class, kept for the
   unrelated reason in Scope/forbid. While in `daemon.py` and
   `effects_gates.py`, fix the two module/class docstrings this deletion
   makes wrong: `daemon.py:75` currently says "families (VerifyGate,
   RunPostMergeGate)" — drop the now-singular "families" framing and the
   `VerifyGate` mention; `effects_gates.py`'s module and `GateEffector`
   docstrings (~lines 1-49, 121) similarly describe "both gate families" —
   reword to reflect that only the post-merge family remains. Neither is
   oracle-checked; do them anyway, they are direct, low-cost fallout of
   this same change.
4. **Update every test file that references the removed GA1/GA4 surface**
   (`test_cli.py`, `test_daemon.py`, `test_effects.py`, `test_invariants.py`,
   `test_planning.py`, `effect_differential.py`,
   `corpus_profiles.py`, `test_snapshot_faults.py`; **NOT**
   `legacy_planner.py`, forbidden — see Scope/forbid; `test_gap_audit.py`
   needs no edit, see its scope.touch entry):
   remove every test/fixture asserting the removed GA4 *behavior*
   (`VerifyGate` being emitted/consumed, the daemon cadence firing,
   `GATE_VERIFY_RECORDED` being recorded) or referencing `drain_verify`,
   `_run_verify_probe`, `cmd_gate_verify`, `gate_canary`, or `coverage_gate`
   — including `corpus_profiles.py`'s `"gate-verify-due"` profile tuple
   (delete the entry outright; the cadence it exercises no longer exists —
   this is a scenario-relevance deletion, not a fix for a construction
   error, since both `gate_verify_interval_days` and `days_since_gate_verify`
   remain valid kwargs) and `test_snapshot_faults.py`'s module-level
   `IRREVERSIBLE` tuple (drop the `EventType.GATE_VERIFY_RECORDED` member —
   this is a collection-time constant, so a stray reference here fails
   before any test body even runs). **Do not** remove a bare
   `gate_verify_interval_days=`/`days_since_gate_verify=` kwarg from any
   fixture just because the name matches — both are still real, valid
   fields; only remove them where the surrounding test is actually
   asserting the now-deleted scheduling behavior (as `corpus_profiles.py`'s
   `"gate-verify-due"` entry does). Verify `planner_corpus.py` and
   `test_planner_differential.py` still pass after that deletion — they
   consume `corpus_profiles.PROFILES` generically and should need no edit
   unless either special-cases `"gate-verify-due"` by name, in which case
   remove that special case too. Verify `test_remote_mutation_audit_tools.py`
   stays green untouched (its `worker` fixture loads
   `tools/remote_mutation_audit.py` dynamically; once that file's import
   points at `mutants.py`, nothing here needs to change). **Do not touch**
   the six `test_mutation_gate_*` functions in `test_daemon.py` (~lines
   6136-6390: `test_mutation_gate_pass_merges`,
   `test_mutation_gate_failure_rejects`, `test_mutation_gate_disabled_skips`,
   `test_mutation_gate_no_declared_gate_skips`,
   `test_mutation_gate_timeout_expired`, `test_mutation_gate_oserror`) —
   they exercise `effects_merge.py`'s kept generic wiring via a plain
   `argv=['true']`/`argv=['false']` `GateDef`, never the deleted module.
5. **Fix both stale onboarding references and the scaffold default.** In
   `onboarding_gate.py`, fix TWO separate mentions: the missing-gate
   guidance text (~lines 45-56, drop the "ecosystem `coverage_gate.py`"
   clause and the "run `nyxloom gate verify <project>`" sentence, keeping
   the `cargo llvm-cov`/`nyc` examples) AND `_has_gate_recommendation`
   (~lines 60-69, which separately says "run `nyxloom gate verify
   <project>` yourself..." and cites `gate_verify_interval_days` by name —
   rewrite or remove this recommendation, it cannot survive pointing at a
   deleted command and a deleted config field). In `gate_scaffold.py`,
   drop the `"python -m nyxloom.coverage_gate --base main --coverage-json
   ... "` line from the scaffolded `inner` command (leave the plain
   pytest+coverage-JSON invocation, without the nyxloom-side judging
   step), change `asserts=["tests-pass", "changed-line-coverage"]` to
   `asserts=["tests-pass"]` (the scaffold no longer measures a coverage
   floor itself), and replace the `ADJUST_MARKER` trailing comment's
   coverage-floor advice with a pointer to adopting `run-gate`+`assay`
   (`run-gate-project/CONSUMERS.md`, `assay/docs/CONSUMERS.md`) for real
   rigor.
6. **NL-4 — add `"assay-verdict"` to the asserts schema enum, all three
   copies.** In `src/nyxloom/schemas/nyxloom-config.schema.json`, add
   `"assay-verdict"` to the `asserts` array's `enum` (alongside the
   existing `tests-pass`, `changed-line-coverage`, `mutation`,
   `canary-verified`). Mirror the addition in `config.py`'s `GateDef.
   asserts` docstring comment listing the same enum (config.py:64-65), and
   in the same docstring (config.py:66-70) remove the now-stale sentence
   "`nyxloom gate verify` cross-checks it against its own observed
   verdict ... see `cli.cmd_gate_verify`" — both name a function this
   package deletes (Work item 2). Do the STANDARD.md mirror as part of
   Work item 7's edit, not separately — the two touch the same paragraph
   and must be done together to avoid the contradiction the first carve
   draft had.
7. **STANDARD.md — one unambiguous edit plan covering all three
   occurrences of "nyxloom gate verify" plus the NL-4 mirror, in this
   order:**
   - **Occurrence 1** (the TRANSPORT_UNTRUSTED bullet, ~line 192): change
     "probe the transport before trusting a verdict — `nyxloom gate
     verify` runs a sentinel first and reports **TRANSPORT_UNTRUSTED**
     (rather than a real gate verdict) when the transport truncates, and
     `nyxloom doctor` fails closed on the same signal" to something like
     "probe the transport before trusting a verdict — `nyxloom doctor`
     fails closed when `transport_check.probe_default()` detects a
     truncating transport." `transport_check.py` is shared, independent
     infrastructure that `doctor.py` uses directly (verified,
     `doctor.py:801`) and is NOT touched by this package — only
     `cmd_gate_verify`'s own use of it goes away.
   - **Occurrence 2** (the "OFFERED, not mandated — the toolkit"
     paragraph, ~lines 199-204): delete this paragraph in full.
   - **Occurrence 3** (the "Gate rigor is a first-class, per-project
     fact" paragraph, ~lines 210-225): this is ONE unbroken paragraph
     containing BOTH the rigor-declaration/routing sentence (keep, and
     apply the NL-4 `assay-verdict` mirror to its `asserts=[...]` list)
     AND the GA1-proving sentences (delete). Keep: "**Gate rigor is a
     first-class, per-project fact.** ... so a project SHOULD declare
     what its gate actually asserts (the `asserts=[tests-pass|
     changed-line-coverage|mutation|canary-verified|assay-verdict]` key
     on `[gates.*]`) — nyxloom surfaces it and routes review depth
     accordingly." Delete: everything from "`nyxloom gate verify` doesn't
     just trust the declaration" through "...is a DECLARATION MISMATCH."
     Keep, with `coverage_gate.py` removed: "Declaring a coverage floor
     is offered, not mandated — advisable wherever the ecosystem supports
     it (`cargo llvm-cov`/`nyc`, or your project's own declared assay/
     run-gate lane), but a project that runs tests without one is still a
     valid consumer; it simply leans harder on the reviewer." Keep the
     trailing sentence about `LESSONS.md` PL2 and `docs/plan-gate-
     adoption.md` as-is (neither names a deleted symbol).
   - **Occurrence 4** ("Validation methodology" item 7, ~line 266): the
     parenthetical "(prove it rejects a known-bad canary
     (`nyxloom gate verify`))" — drop the `nyxloom gate verify` citation;
     the methodology point (separate the run from the verdict, fail
     closed) stands on its own without naming a now-deleted command.
   - Item 1's `gate verify v1` anecdote (~"Worked example: `gate verify`
     v1 passed its own gate...") is a historical incident, already past
     tense, and does not claim the tool currently exists — leave it
     unchanged.
8. **Record the decision.** Add a dated entry (2026-09-02 or later, at
   least 150 words — see O6) to `nyxloom-trove/decisions.md` covering both
   the toolkit deletion's rationale (no project, including nyxloom itself,
   ever imported the modules as a library; nyxloom's own
   `[gates.tester-unified]` runs entirely through `run-gate.py` since
   nyxloom-P48 and never declared a `phase='mutation'` gate that would
   have exercised the toolkit) and GA1/GA4's retirement rationale (Assay's
   own R2/R3 mechanisms supersede external gate-trustworthiness
   verification once a project declares assay/run-gate lanes), citing
   `nyxloom-trove/reports/ASSAY-NYXLOOM-REORIENTATION-2026-08-17.md`'s
   "Deletion inventory and Assay transfer check" section, and explicitly
   naming that this reverses the 2026-07-27 operator directive that
   enabled `mutation_gate` in `nyxloom-trove/nyxloom.toml`. Also record,
   as part of the same entry, that `Policy.gate_verify_interval_days` and
   `ReconcileInput.days_since_gate_verify` are deliberately NOT deleted —
   they stay declared, permanently unread by any live code path, because
   `tests/legacy_planner.py`'s frozen byte-identical differential-testing
   baseline reads them unconditionally off the same production dataclasses
   and cannot itself be edited (Context item 13).
9. **Close the superseded P90 handoff.** Move
   `nyxloom-trove/handoffs/nyxloom-P90-extract-testing-library.md` to
   `nyxloom-trove/archive/nyxloom-P90-extract-testing-library.md`,
   prepending a real explanatory note (not a bare marker — see O7): it is
   superseded by the 2026-08-17 reorientation analysis and closed by
   nyxloom-P98, because it would have recreated testing capability Assay
   now provides.
10. **Fix the ownership inventory `tests/test_core_characterization.py`
    checks against reality — THREE rows, not two; re-measure every row
    this package's edits could plausibly touch, don't stop at the first
    two found.** In
    `nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md`:
    remove the `src/nyxloom/gate_canary.py` row entirely (its path no
    longer exists — `test_inventory_paths_all_exist` fails otherwise).
    Re-measure `src/nyxloom/effects_gates.py`, `src/nyxloom/cli.py`, AND
    `src/nyxloom/rules_attention.py` with `wc -l` on the tree AFTER Work
    items 2, 3, and 5's edits land, and update each recorded line count to
    its real value. `effects_gates.py` (recorded 473) and `cli.py`
    (recorded 2,469) both exceed their own declared tolerance today and
    fail `test_inventory_sizes_are_within_the_declared_tolerance`
    otherwise — do not guess or hardcode a number from this handoff's own
    prose (a carver-predicted number is exactly what got `cli.py`'s
    original row this stale; re-measure for real). `rules_attention.py`
    (recorded 118) is inside its own tolerance floor today but drifts
    further once Work item 3 lands — re-measure it too rather than assume
    it stays under. Trim two rows' "Present responsibility" text to match:
    `effects_gates.py` currently reads "the gate-verify cadence and
    post-merge validation" — the cadence half is gone, only "post-merge
    validation" (and whatever else the file still does) remains accurate;
    `rules_attention.py` currently reads "...and the gate-verify cadence
    that is deliberately outside the carve mutex" — drop that clause, the
    rule itself is deleted (Work item 3). Add a short "Re-measured
    2026-09-02 (nyxloom-P98)" note following the document's own existing
    convention (see its "Re-measured DATE (CR-NN review) for ..."
    paragraphs near the top), explaining all three changes. This file is a
    *live*, mechanically-checked inventory —
    unlike `tests/legacy_planner.py`, it is meant to be kept current, not
    frozen; updating it is the correct fix, not a forbidden edit.

## Scope / forbid

Out of scope — do not touch, even though the names look related:

- **`src/nyxloom/gate_runner.py`** — the generic gate-argv executor
  (`select_verification_gate`, `run_gate_at_commit`). It imports neither
  `coverage_gate` nor `mutation_gate` nor `gate_canary` today and becomes
  the *only* gate-execution path once this package lands. No change
  needed.
- **`src/nyxloom/effects_merge.py`**, specifically the `("mutation",
  getattr(cfg.policy, "mutation_gate", False), effects_gates.
  select_mutation_gate)` post-merge wiring — this is generic: it selects
  whatever `GateDef` a project *declares* with `phase='mutation'` and
  runs its `argv` verbatim, the same as the neighboring `"pre-merge"`
  entry. It has never imported the deleted `mutation_gate.py` module.
  Confirmed by sweep and independently re-verified by the pre-dispatch
  adversarial review: `git grep -n "coverage_gate\|gate_canary" --
  src/nyxloom/effects_merge.py` returns nothing; the file's only
  "mutation_gate" hit is the `cfg.policy.mutation_gate` boolean attribute
  read, which is the kept opt-in toggle, not a module reference.
- **`src/nyxloom/config.py`'s `Policy.mutation_gate: bool` field** (the
  F017 opt-in toggle consumed by the wiring above; **note it lives on
  `Policy`, not `GateDef`** — an early carve draft misattributed this
  three times) — stays, for a reason unrelated to the one below.
- **`src/nyxloom/config.py`'s `Policy.gate_verify_interval_days` field
  and `src/nyxloom/reconcile.py`'s `ReconcileInput.days_since_gate_verify`
  field** — **both stay declared, unedited, in files this package
  otherwise touches.** This is not the same case as `Policy.mutation_gate`
  above (that one is still genuinely used by kept orchestration); these
  two become permanently dead — nothing in the live path reads either one
  once Work item 3's scheduling removal lands. They are kept anyway
  because **`tests/legacy_planner.py`** (below) reads them unconditionally
  off the same production `Policy`/`ReconcileInput` instances the live
  planner consumes, and that file's own byte-identity self-check forbids
  editing it to cope. Deleting either field passes every oracle except
  O8's explicit (and deliberately inverted) presence check for exactly
  this pair — see O8.
- **`tests/legacy_planner.py`** — a mechanically self-verified,
  byte-identical copy of `reconcile.py` at commit `052857ae` (its own
  header: "DO NOT EDIT THIS MODULE TO MAKE A TEST PASS").
  `tests/test_planner_differential.py::
  test_legacy_baseline_is_the_committed_branch_point` asserts this file,
  after undoing its two declared import-only edits, is byte-identical to
  `git show 052857ae:...reconcile.py`. This is the file that makes the two
  fields above un-deletable; do not edit it for any reason this package
  raises.
- **`tests/test_daemon.py`'s six `test_mutation_gate_*` functions**
  (~lines 6136-6390) — these exercise the kept `effects_merge.py` wiring
  via a bare `argv=['true']`/`argv=['false']` `GateDef`, not the deleted
  module. Leave them exactly as they are.
- **`tests/test_remote_mutation_audit_tools.py`** — dynamically exec's
  `tools/remote_mutation_audit.py` rather than importing
  `nyxloom.mutation_gate` directly; needs no edit once that file's own
  import is updated (Work item 1). Verify it stays green; do not edit it.
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
