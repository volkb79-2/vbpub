# nyxloom-P98 -- implementation REPORT

**Status: BLOCKED.** Work items 1-9 are fully implemented and committed
(12 commits, `ec02107f`..`918c3a03`, see LOG.md). Oracles O1, O3, O4, O5, O6,
O7, O8, O9 all pass, verified below with real command output. **O2 (the
`tester-unified` gate passes green) does not pass** -- two real, reproducible
pytest failures remain, both rooted in a single stale row in a non-test
report file that is outside this package's `scope.touch`, not named
anywhere in the handoff, its 13-item Context list, or either of the two
prior adversarial review rounds. See "BLOCKED finding" below for the full
diagnosis and why this is `escalate_if` #1 firing rather than something to
fix by editing that file myself.

## Independent sweep (before touching anything)

Per the dispatch instructions, ran my own `git grep` sweep for the three
module-name tokens across `src/`, `tests/`, `tools/` BEFORE Work item 1's
deletions, and cross-checked every hit against a specific Work item:

| Token | Hits found | Disposition |
|---|---|---|
| `coverage_gate` | `src/nyxloom/{coverage_gate,mutation_gate}.py` (self-refs, deleted file), `src/nyxloom/{gate_scaffold,onboarding_gate}.py` (Work item 5), `tests/test_{coverage_gate,mutation_gate}.py` (Work item 1), `tools/assay/assay-4.0.0.pyz` (binary, third-party zipapp, irrelevant) | all accounted for |
| `mutation_gate` | `src/nyxloom/cli.py` help text (Work item 2), `src/nyxloom/config.py` (`Policy.mutation_gate` -- Scope/forbid, kept), `src/nyxloom/effects_gates.py`/`effects_merge.py` (generic wiring -- Scope/forbid, kept), `src/nyxloom/gate_canary.py`/`mutation_gate.py` self-refs (Work item 1), schema `mutation_gate` boolean property (unrelated, kept), `tests/test_daemon.py`'s six kept `test_mutation_gate_*` (Scope/forbid), `tests/test_mutation_gate.py` (Work item 1), `tests/test_reviewer_repair.py:198` (unrelated comment about `Policy.mutation_gate`'s defaults, verified false-positive), `tools/remote_mutation_audit.py` (Work item 1) | all accounted for |
| `gate_canary` | `src/nyxloom/cli.py` (Work item 2), `src/nyxloom/daemon.py`/`effects_gates.py` (Work item 3), `tests/test_cli.py` (Work item 4), `tests/test_gate_canary.py`/`test_gate_verify_cadence.py` (Work item 1), `tests/test_invariants.py:740` (Work item 4) | all accounted for -- **note:** this three-directory sweep does NOT cover `nyxloom-trove/`, which is where the actual blocking reference lives (see BLOCKED finding) |

No `escalate_if` #1/#2 trigger fired on this three-token sweep as scoped.
The extraction boundary re-check (`escalate_if` #3) was independently
re-verified: `generate_mutants` (lines 96-270) calls nothing in
`mutation_gate.py`'s gate-judgment half, confirmed by reading the full
function body before extracting.

Two additional symbol-level findings surfaced during implementation, both
resolved as in-scope mechanical completions of already-instructed edits
(not escalated -- see LOG.md commits `c105767a` and `17d21b2e` for the
reasoning): `reconcile.TRACE_KINDS`'s `"gate-verify"` breadcrumb entry
(caught by re-deriving `test_the_breadcrumb_vocabulary_is_exactly_what_the_rules_can_record`'s
bidirectional check before it ran), and `onboarding_gate.py`'s third
"nyxloom gate verify" mention in its module docstring (O9's `grep -c` is a
whole-file count, not scoped to the two named blocks).

## Oracle evidence

### O1 -- the seven deleted paths are gone; mutants.py exists

```
$ git ls-files | grep -E "^(src/nyxloom/(coverage_gate|mutation_gate|gate_canary)\.py|tests/test_(coverage_gate|mutation_gate|gate_canary|gate_verify_cadence)\.py)$"
(no output)
$ git ls-files | grep "src/nyxloom/mutants.py"
src/nyxloom/mutants.py
```
PASS.

### O2 -- the gate passes green

**FAIL.** Full gate argv run twice (see LOG.md "Gate runs"). Final state
(commit `918c3a03`): `tester-unified: FAIL/COMMAND_FAILED (exit 1)`, exactly
two pytest failures, both in `tests/test_core_characterization.py`:
`test_inventory_paths_all_exist` and
`test_inventory_sizes_are_within_the_declared_tolerance`. Full diagnosis in
"BLOCKED finding" below. All six named collection targets DO collect and
run (confirmed in both gate runs' stdout, and independently via
`pytest --collect-only` locally): `test_planning.py`, `test_gap_audit.py`,
`test_daemon.py`, `test_remote_mutation_audit_tools.py`,
`test_snapshot_faults.py`, `test_planner_differential.py` (including
`test_legacy_baseline_is_the_committed_branch_point` and the full
`PROFILES` parametrization -- confirmed by direct run, see below).

```
$ PYTHONPATH=src python3 -m pytest tests/test_planner_differential.py::test_legacy_baseline_is_the_committed_branch_point -q
.                                                                        [100%]
```

### O3 -- no canary-probing subcommand under `gate`

```
$ PYTHONPATH=src python3 -m nyxloom.cli gate --help
...
usage: nyxloom gate [-h] {} ...

positional arguments:
  {}

options:
  -h, --help  show this help message and exit
```
Zero subcommands registered under `gate` (empty `{}` choice set) -- no
`verify`, no renamed equivalent. (The double-help-block-print / exit-code-2
wrapper behavior visible above is pre-existing in `cli.main()`'s
`except (SystemExit, ...)` handler for EVERY subcommand's `--help`, not
something this package introduced or broke -- confirmed identical for
`nyxloom --help` and `nyxloom project --help`.) PASS.

### O4 -- assay-verdict in the schema enum + two doc mirrors

```
$ python3 -c "import json; d=json.load(open('src/nyxloom/schemas/nyxloom-config.schema.json')); print('assay-verdict' in d['properties']['gates']['additionalProperties']['properties']['asserts']['items']['enum'])"
True
$ grep -c "assay-verdict" src/nyxloom/config.py
1
$ grep -c "assay-verdict" reference/STANDARD.md
1
```
PASS.

### O5 -- STANDARD.md's three occurrences resolved correctly

```
$ grep -c "OFFERED, not mandated -- the toolkit" reference/STANDARD.md
0
$ grep -c "nyxloom gate verify" reference/STANDARD.md
0
$ grep -c "asserts=\[tests-pass" reference/STANDARD.md
1
```
PASS.

### O6 -- decisions.md entry

```
$ python3 -c "
text = open('nyxloom-trove/decisions.md').read()
entry = text.split('## 2026-09-02')[1]
print('word count:', len(entry.split()))
print('2026-07-27 + mutation_gate:', '2026-07-27' in text and 'mutation_gate' in text)
print('run-gate.py + nyxloom-P48:', 'run-gate.py' in text and 'nyxloom-P48' in text)
print('GA1 + GA4:', 'GA1' in text and 'GA4' in text)
print('cites reorientation report:', 'ASSAY-NYXLOOM-REORIENTATION-2026-08-17.md' in text)
print('cites Deletion inventory section:', 'Deletion inventory and Assay transfer check' in text)
"
word count: 275
2026-07-27 + mutation_gate: True
run-gate.py + nyxloom-P48: True
GA1 + GA4: True
cites reorientation report: True
cites Deletion inventory section: True
```
PASS (275 words, well over the 150-word floor; both required facts named
with reasoning, not keyword-stuffed).

### O7 -- P90 archived with a real note

```
$ test -f nyxloom-trove/handoffs/nyxloom-P90-extract-testing-library.md && echo EXISTS || echo GONE
GONE
$ test -f nyxloom-trove/archive/nyxloom-P90-extract-testing-library.md && echo EXISTS
EXISTS
$ head -15 nyxloom-trove/archive/nyxloom-P90-extract-testing-library.md
SUPERSEDED 2026-09-02, closed by nyxloom-P98 (not dispatched). This proposal
would have extracted nyxloom's `coverage_gate.py`/`mutation_gate.py`/
`gate_canary.py` cluster into a standalone testing/rigor library so other
projects could consume it without adopting nyxloom -- exactly the capability
the estate's separately-built Assay tool (see this proposal's own §Naming,
which floated "assay" as the library's name) now provides directly. Rather
than build that library, nyxloom-P98 deletes the toolkit modules outright
(nyxloom-trove/reports/ASSAY-NYXLOOM-REORIENTATION-2026-08-17.md's "Deletion
inventory and Assay transfer check") and retires GA1/GA4's canary-based
gate-trustworthiness verification in favor of Assay's own R2/R3 mechanisms.
Archived verbatim below for its diagnostic value (the four-way
`coverage_gate.py` duplication evidence, the LanguageAdapter design) even
though its "extract a library" conclusion is moot.

---
```
PASS (prose note, first 13 lines, names nyxloom-P98 and the specific reason).

### O8 -- symbol-level introspection script

Full source (also saved during this session; reproduced verbatim here):

```python
#!/usr/bin/env python3
"""nyxloom-P98 Oracle O8 -- symbol-level introspection, not string grep.

Asserts the GA1/GA4 symbols are gone (via hasattr/dataclasses.fields
introspection, so relocation-under-a-new-name does not fool it) AND,
symmetrically, that the two deliberately-kept fields
(Policy.gate_verify_interval_days, ReconcileInput.days_since_gate_verify)
are STILL present -- tests/legacy_planner.py's frozen byte-identity
differential-testing baseline reads them unconditionally off the same
production dataclass instances the live planner consumes.
"""
from __future__ import annotations

import dataclasses
import sys


def check(label: str, condition: bool) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    TOTAL.append(label)
    if not condition:
        FAILURES.append(label)


TOTAL: list[str] = []
FAILURES: list[str] = []

sys.path.insert(0, "src")

from nyxloom import reconcile  # noqa: E402
from nyxloom import effects_gates  # noqa: E402
from nyxloom import types  # noqa: E402
from nyxloom import rules_attention  # noqa: E402
from nyxloom import planning  # noqa: E402
from nyxloom import config  # noqa: E402

# --- GA1/GA4 symbols must be GONE -------------------------------------

check(
    "reconcile.VerifyGate does not exist",
    not hasattr(reconcile, "VerifyGate"),
)

check(
    "effects_gates.GateEffector has no verify_gate attribute",
    not hasattr(effects_gates.GateEffector, "verify_gate"),
)
check(
    "effects_gates.GateEffector has no _run_verify_probe attribute",
    not hasattr(effects_gates.GateEffector, "_run_verify_probe"),
)
check(
    "effects_gates.GateEffector has no drain_verify attribute",
    not hasattr(effects_gates.GateEffector, "drain_verify"),
)

check(
    "types.EventType has no GATE_VERIFY_RECORDED member",
    not hasattr(types.EventType, "GATE_VERIFY_RECORDED"),
)

check(
    "rules_attention module has no gate_verify attribute",
    not hasattr(rules_attention, "gate_verify"),
)

# rule_table() must not raise, and no spec may reference gate_verify /
# a gate-verify-cadence channel.
try:
    table = planning.rule_table()
    rule_table_raised = False
except Exception as exc:  # noqa: BLE001
    table = ()
    rule_table_raised = True
    print(f"    (rule_table() raised: {type(exc).__name__}: {exc})")

check(
    "planning.rule_table() does not raise",
    not rule_table_raised,
)
check(
    "no rule_table() spec's rule is rules_attention.gate_verify (moot -- attribute already gone)",
    not any(
        getattr(spec, "rule", None) is getattr(rules_attention, "gate_verify", object())
        for spec in table
    ),
)
check(
    "no rule_table() spec references a gate-verify-cadence channel by name",
    not any(getattr(spec.channel, "value", "") == "gate-verify-cadence" for spec in table),
)

# --- the two deliberate exceptions must STILL be present ---------------

policy_fields = [f.name for f in dataclasses.fields(config.Policy)]
check(
    "config.Policy still declares gate_verify_interval_days",
    "gate_verify_interval_days" in policy_fields,
)

reconcile_input_fields = [f.name for f in dataclasses.fields(reconcile.ReconcileInput)]
check(
    "reconcile.ReconcileInput still declares days_since_gate_verify",
    "days_since_gate_verify" in reconcile_input_fields,
)

print()
if FAILURES:
    print(f"O8: FAIL ({len(FAILURES)}/{len(TOTAL)} check(s) failed): {FAILURES}")
    sys.exit(1)
print(f"O8: PASS -- all {len(TOTAL)} checks passed")
sys.exit(0)
```

Actual output, run from the worktree root at commit `918c3a03`:

```
[PASS] reconcile.VerifyGate does not exist
[PASS] effects_gates.GateEffector has no verify_gate attribute
[PASS] effects_gates.GateEffector has no _run_verify_probe attribute
[PASS] effects_gates.GateEffector has no drain_verify attribute
[PASS] types.EventType has no GATE_VERIFY_RECORDED member
[PASS] rules_attention module has no gate_verify attribute
[PASS] planning.rule_table() does not raise
[PASS] no rule_table() spec's rule is rules_attention.gate_verify (moot -- attribute already gone)
[PASS] no rule_table() spec references a gate-verify-cadence channel by name
[PASS] config.Policy still declares gate_verify_interval_days
[PASS] reconcile.ReconcileInput still declares days_since_gate_verify

O8: PASS -- all 11 checks passed
```
PASS.

### O9 -- onboarding_gate.py / gate_scaffold.py / config.py stale-reference cleanup

```
$ grep -n "coverage_gate" src/nyxloom/onboarding_gate.py src/nyxloom/gate_scaffold.py
(no output)
$ grep -c "nyxloom gate verify" src/nyxloom/onboarding_gate.py
0
$ grep -c "cmd_gate_verify" src/nyxloom/config.py
0
```
PASS.

## BLOCKED finding: `escalate_if` #1 firing

**Root cause:** `nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md`
(a frozen, mechanically-checked module-ownership inventory from the earlier
CORE REDESIGN programme) carries a row for `src/nyxloom/gate_canary.py`
(line 123: `| \`src/nyxloom/gate_canary.py\` | 402 | CR-02, CR-12: ... |`).
`tests/test_core_characterization.py` reads this table
(`_inventory_rows()`, `INVENTORY_PATH`) and asserts every listed path still
exists (`test_inventory_paths_all_exist`) and every listed size is within a
`max(40, 10%)` tolerance of the real file's current line count
(`test_inventory_sizes_are_within_the_declared_tolerance`). Deleting
`gate_canary.py` (Work item 1, required) makes the first assertion fail
outright, and the second raises `FileNotFoundError` trying to `read_text()`
a path that no longer exists -- before it ever reaches the *next* real
problem the same table has: `src/nyxloom/effects_gates.py`'s row (line 68)
records `473` lines; Work item 3's deletions leave it at `354` (see
`git log`), a 119-line drift against a `max(40, 35)=40`-line tolerance, so
even after `gate_canary.py`'s row is dealt with, `effects_gates.py`'s row
would independently fail the same test.

**Why this is `escalate_if` #1, not mine to fix:**
- `nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md` is
  a **non-test file**, and it is **not in `scope.touch`** -- it appears
  nowhere in the handoff frontmatter, the 13-item Context list, or either of
  the two prior adversarial review rounds (`nyxloom-P98-CARVE-REVIEW.md`,
  `nyxloom-P98-FIX-VERIFICATION{,-2}.md`). `escalate_if` #1's own wording is
  exact: *"any touched non-test file outside this list needs an edit to
  keep the gate green (a reverse-dependency this carve's sweep missed) ...
  a NEW miss surfacing at dispatch means the sweep needs a third pass, not
  implementer improvisation."*
- The instructed three-token sweep (`coverage_gate`/`mutation_gate`/
  `gate_canary` across `src/`, `tests/`, `tools/`) would never have found
  this: the reference lives in `nyxloom-trove/reports/`, a directory
  outside that sweep's scope by the dispatch instructions' own definition
  -- yet it is a real, literal `gate_canary` path reference that breaks the
  gate. This is structurally the same shape of miss as the
  `tests/legacy_planner.py` bind the prior implementer dispatch on this
  exact package correctly escalated: a real reverse-dependency neither
  review round's sweep depth reached.
- `escalate_if` #4 (about item 16/17 renumbering) establishes the operative
  precedent for exactly this situation: *"if leaving the gap breaks a test
  that asserts contiguous item numbers, that is a genuine BLOCKED, not
  something to paper over by renumbering out-of-scope files."* The same
  reasoning applies here: leaving this out-of-scope report file stale
  breaks two real tests; editing it myself to "paper over" that would be
  exactly the improvisation the escalate_if mechanism exists to prevent.
- Two other test-level gaps surfaced by the same real gate run
  (`test_planning.py`'s contract-item-16 completeness check,
  `test_effect_differential.py`'s frozen fixture) were fixed directly
  because both files were **already in `scope.touch`** for exactly this
  class of edit, and the fixes were single-file, test-only, and mechanical
  continuations of already-instructed Work items -- a materially different
  situation from a brand-new non-test file nobody named.

**What the fix would look like** (for the carve repair, not attempted
here): update `CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md` line 123
(remove or retire the `gate_canary.py` row) and line 68 (re-measure
`effects_gates.py`'s line count, and likely its descriptive text, which
still describes "the gate-verify cadence and post-merge validation" as
current). Whether that inventory should also gain a P98-dated addendum
note (mirroring how `decisions.md` records this package) or simply drop the
row silently is a carve-level editorial call, not something to guess at
mid-implementation.

## Test-file pruning itemization (why each removal is safe)

- **`tests/test_coverage_gate.py`, `tests/test_gate_canary.py`,
  `tests/test_gate_verify_cadence.py`** -- deleted whole; each is dedicated,
  100%, to a module deleted in the same commit.
- **`tests/test_mutation_gate.py`** -- 27 of its cases (the
  "`generate_mutants` -- pure AST mutation" section, lines 27-317 minus
  `test_falsy_swap_motivating_survivor`) moved verbatim to
  `tests/test_mutants.py` with only the import retargeted. The rest (all
  `evaluate`/`_run_is_killed*`/`main`/CLI-argument-parsing tests) deleted --
  they test the retired gate-judgment half, not `generate_mutants` itself.
  `test_falsy_swap_motivating_survivor` specifically stayed with the
  deleted set because it calls `mg._run_is_killed` in addition to
  `generate_mutants`.
- **`tests/test_cli.py`** -- deleted `cmd_gate_verify`'s entire test
  surface (GA1 verdict-logic, transport-preflight, GA2 asserts-mismatch,
  GA2b coverage-canary sections: ~35 functions, the `canary_project`/
  `multi_file_canary_project`/`no_gate_project` fixtures, the
  `_fake_gate_result`/`_coverage_gate`/`_fake_run_by_phase` helpers) and the
  now-dead autouse `_healthy_transport_by_default` fixture (nothing in
  `cli.py` calls `transport_check.probe_default` any more). Kept
  `test_gate_no_subcommand_prints_help_and_exits_2` (the `gate` verb itself
  survives, now with zero subcommands) and the two GA2 schema/dataclass
  tests (`test_every_gatedef_field_is_toml_settable_or_explicitly_infra_sourced`,
  `test_gatedef_asserts_defaults_to_empty_list`), rewording the latter's
  stale docstring mention of `cli._asserts_mismatch_report` to the real
  remaining consumers.
- **`tests/test_effects.py`, `tests/effect_differential.py`** -- removed
  only the `VerifyGate`/`verify-gate-dispatch` sample-table entries; every
  other entry (including the still-live `RunPostMergeGate`) is untouched.
- **`tests/test_invariants.py`, `tests/test_snapshot_faults.py`** --
  removed exactly the one `EventType.GATE_VERIFY_RECORDED` member from each
  of `KNOWN_IGNORED_EVENT_TYPES` and `IRREVERSIBLE` (both module-level
  collection-time constants).
- **`tests/test_planning.py`** -- deleted
  `test_the_gate_verify_cadence_claims_nothing` (asserted the removed
  `rule_table()` entry by name). Updated
  `test_every_rule_names_a_real_contract_item`'s expected contract-item set
  from `{8}` excluded to `{8, 16}` excluded (see BLOCKED-adjacent fix in
  LOG.md commit `1c455ca7`). Left the 4 unrelated `emits=frozenset({"VerifyGate"})`
  probe-spec usages alone -- verified `RuleSpec.__post_init__` only
  validates `emits` against `EXCLUSIVE_ACTIONS` (the carve family), so these
  are harmless synthetic string labels, never an attribute reference to
  `reconcile.VerifyGate`.
- **`tests/corpus_profiles.py`** -- deleted the `"gate-verify-due"` profile
  tuple outright (a scenario-relevance deletion, not a compile fix -- both
  fields it set remain valid `Policy`/`ReconcileInput` kwargs).
- **`tests/fixtures/effect_transcripts_v1.json`** -- re-recorded per its own
  documented `NYXLOOM_RECORD_EFFECT_TRANSCRIPTS=1` workflow; the diff is
  exactly the one-entry removal `"verify-gate-dispatch": []`, nothing else
  moved.
- **`tests/test_daemon.py`, `tests/test_gap_audit.py`,
  `tests/test_remote_mutation_audit_tools.py`, `tests/planner_corpus.py`,
  `tests/test_planner_differential.py`, `tests/test_onboarding_gate.py`,
  `tests/test_gate_scaffold.py`** -- `test_daemon.py` verified to have zero
  actual `VerifyGate`/`GATE_VERIFY_RECORDED`/`gate_canary` test content
  (only four comment mentions of the sibling deleted file's name, describing
  the still-kept post-merge-gate tests by design symmetry -- left as-is,
  not oracle-relevant); the four generic-corpus consumers verified
  unaffected by the `corpus_profiles.py` deletion; `test_onboarding_gate.py`
  and `test_gate_scaffold.py` fixed for the prose/asserts changes in Work
  item 5 (not named in `scope.touch` but a direct, single-assertion
  mechanical consequence of an already-instructed edit -- see LOG.md
  commit `17d21b2e`).
- **`tests/test_mutants.py`** (new) -- added one case,
  `test_falsy_swap_ignores_a_non_literal_return_value`, closing a real
  changed-line-coverage gap in the new `mutants.py` (line 98's fallback,
  previously exercised only via the deleted CLI's `main()` tests).

## Doc files touched

| File | What changed |
|---|---|
| `reference/STANDARD.md` | Occurrence 1 (TRANSPORT_UNTRUSTED bullet): reworded to cite `nyxloom doctor`/`transport_check.probe_default()` directly. Occurrence 2 ("OFFERED, not mandated"): paragraph deleted in full. Occurrence 3 ("Gate rigor is a first-class fact"): kept + edited the rigor-declaration sentence (NL-4 mirror) and the coverage-floor sentence (`coverage_gate.py` -> "your project's own declared assay/run-gate lane"); deleted the GA1-proving sentences in between; left the trailing LESSONS.md/plan-gate-adoption.md sentence as-is per the handoff. Occurrence 4 (Validation methodology item 7): dropped the `nyxloom gate verify` citation. Item 1's historical "gate verify v1" anecdote left unchanged. |
| `assay.toml` | Header comment corrected: no longer claims `mutation_gate.py` "remains the toolkit nyxloom OFFERS other projects" (false once deleted); describes the actual outcome (outright deletion, `mutants.py` survives only for `tools/remote_mutation_audit.py`'s own use). |
| `src/nyxloom/config.py` | `GateDef.asserts` docstring: enum list mirrors NL-4's `assay-verdict`; removed the stale `cli.cmd_gate_verify` cross-check sentence. `Policy.gate_verify_interval_days` and `Policy.mutation_gate` left completely unedited (Scope/forbid). |
| `src/nyxloom/onboarding_gate.py` | Module docstring (3rd "nyxloom gate verify" mention, GA4 reference) reworded. `_NO_GATE_RECOMMENDATION`: dropped the `coverage_gate.py` clause and the "run nyxloom gate verify" sentence, kept cargo llvm-cov/nyc. `_has_gate_recommendation`: rewritten to point at assay/run-gate adoption instead of the deleted command and the deleted cadence field. |
| `src/nyxloom/gate_scaffold.py` | Module docstring's two GA1 mentions reworded. `render_gate_def`'s docstring + `inner` argv: dropped the `coverage_gate` invocation line. `asserts` down to `["tests-pass"]` (docstring + actual `GateDef`, two call sites). Outer ADJUST_MARKER comment: dropped the now-stale `--source` placeholder mention, added a run-gate+assay pointer. |
| `nyxloom-trove/decisions.md` | New 275-word dated entry (2026-09-02), both required facts + citation + field-exception rationale (see O6 above). |
| `src/nyxloom/daemon.py` docstring | Module docstring's "two gate families (VerifyGate, RunPostMergeGate)" line reworded to the singular remaining family. |
| `src/nyxloom/effects_gates.py` docstring | Module and `GateEffector` class docstrings reworded from "both gate families" to post-merge-only, with a short retirement note. |
| `src/nyxloom/rules_attention.py` docstring | Reworded from "module contract items 6, 7 and 16" / "the gate-verify cadence runs a subprocess probe" to the two remaining items, noting nyxloom-P98's retirement of the third. |
| `src/nyxloom/reconcile.py` | Item 17's cross-reference to item 16 edited (drops the `gate_verify_interval_days` half of the field-name comparison). `ReconcileInput.days_since_gate_verify`'s own field docstring (mentioning `GATE_VERIFY_RECORDED`/`daemon._days_since_gate_verify`) is left **completely unedited** per Scope/forbid, even though it now describes deleted machinery -- this is the deliberate exception, not an oversight. |

## Orientation telemetry

- Read in full: the handoff (587 lines incl. frontmatter), `decisions.md`
  (1 line, nothing to skim), `nyxloom-P98-CARVE-REVIEW.md` (274 lines),
  `nyxloom-P98-FIX-VERIFICATION.md` (75 lines),
  `nyxloom-P98-FIX-VERIFICATION-2.md` (74 lines), `tests/legacy_planner.py`'s
  header + cadence block, `tests/test_planner_differential.py`'s
  `test_legacy_baseline_is_the_committed_branch_point`, the P90 handoff
  (203 lines) before archiving it.
- Independent sweep: 3 token-level `git grep` passes (`coverage_gate`,
  `mutation_gate`, `gate_canary`) across `src/`, `tests/`, `tools/`, plus 6
  targeted symbol-level greps (`GATE_VERIFY_RECORDED`, `VerifyGate\b`,
  `_run_verify_probe`, `drain_verify`, `\bverify_gate\b`,
  `days_since_gate_verify`, `gate_verify_interval_days`) across the same
  three directories before and after editing, to build the complete
  edit-site table used to plan Work items 1-4 (see the module-by-module
  read-throughs of `mutation_gate.py`, `effects_gates.py`, `daemon.py`,
  `reconcile.py`, `rules_attention.py`, `planning.py`, `types.py`,
  `onboarding_gate.py`, `gate_scaffold.py`, `config.py`,
  `nyxloom-config.schema.json` before touching any of them).
- 12 commits, 2 full gate runs (each `docker update --cpus=3`'d within
  seconds of container start, per the shared-host load rule), both read in
  a separate step from the run itself (no piped tail/grep on the run
  command).
- Approximate tool-call count at BLOCKED: comfortably under the ~60-call/
  120k-context checkpoint threshold for a single dispatch; no checkpoint
  brief was needed.

## Closing

All 9 Work items are implemented and committed. 8 of 9 oracles (O1, O3-O9)
pass, verified above with real command output, not asserted. **O2 does not
pass** -- the gate is red because of a single, precisely diagnosed,
out-of-scope reverse-dependency (see "BLOCKED finding"), not because of
anything within this package's `scope.touch`. I did not edit
`nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md`, and
I am not merging anything.

**BLOCKED: `nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md`
(lines 68 and 123) is a non-test file outside this package's `scope.touch`
whose stale rows (a deleted `gate_canary.py` path; `effects_gates.py`'s
line count, 473 recorded vs. 354 actual) make
`tests/test_core_characterization.py::test_inventory_paths_all_exist` and
`::test_inventory_sizes_are_within_the_declared_tolerance` fail on a real
`./run-gate.py tester-unified` run. This is `escalate_if` #1 firing: a real
reverse-dependency neither the carve's three-token sweep (scoped to
`src/`/`tests/`/`tools/`, not `nyxloom-trove/`) nor two rounds of
adversarial review surfaced. The carve needs a repair round adding this
file (or a replacement row/removal instruction for it) to `scope.touch`
before this package can reach a green gate.**
