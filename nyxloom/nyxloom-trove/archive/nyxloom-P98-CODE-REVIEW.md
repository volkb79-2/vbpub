# nyxloom-P98 — code review (diff vs. frozen handoff)

**Reviewed:** `git diff 286a4bc0..b7daa66f -- .` (branch `feat/nyxloom-P98-retire-toolkit-gate-verify`,
tip `b7daa66f`). **Handoff:** `nyxloom-trove/handoffs/nyxloom-P98-retire-toolkit-gate-verify.md`,
last touched at `580ab61c` (unmodified since, confirmed by `git log`). **Method:** blind pass over
the diff first (re-deriving every oracle and probing evasions before reading the implementer's own
LOG/REPORT), then reconciled against `nyxloom-P98-LOG.md`/`nyxloom-P98-REPORT.md`.

## Verdict: ACCEPT

No blockers. Two non-blocking, cosmetic observations (§7). This is the strongest package I've seen
across the five review rounds on this handoff — every claim I independently re-derived matched what
the implementer reported, including two genuine "caught by actually running the gate" fixes and one
correctly-escalated BLOCKED that the coordinator confirmed and repaired.

## 1. Consumer/call-site dimension — re-tabulated independently

The prior three carve-review rounds found real call sites the original sweep missed
(`planning.py`'s `RuleSpec`, `tools/remote_mutation_audit.py`, `tests/test_snapshot_faults.py`,
`tests/test_gap_audit.py`, `tests/corpus_profiles.py` + consumers, `CORE-REDESIGN-OWNERSHIP-INVENTORY...md`).
Re-ran the same class of check against the **implemented** tree, not just the handoff text:

| Symbol removed | Real reference before | Diff addresses it? | Verified |
|---|---|---|---|
| `mutation_gate.py` (whole) | `tools/remote_mutation_audit.py:33` | Retargeted to `nyxloom.mutants` | `git diff` shows the one-line import change; `wc -l mutants.py`=233, contains `Mutant`+`generate_mutants` only |
| `rules_attention.gate_verify` | `planning.py`'s `RuleSpec(name="gate-verify", rule=rules_attention.gate_verify, ...)` | `RuleSpec` entry deleted | `git diff planning.py` confirms; live `planning.rule_table()` call does not raise (re-run, below) |
| `EventType.GATE_VERIFY_RECORDED` | `test_snapshot_faults.py`'s `IRREVERSIBLE`, `test_invariants.py`'s `KNOWN_IGNORED_EVENT_TYPES`, `effects_gates.py`'s handler spec, `daemon.py` | All four edited | Confirmed in diff; `hasattr(types.EventType, "GATE_VERIFY_RECORDED")` is `False` |
| `ReconcileInput.days_since_gate_verify` kwarg use | `test_gap_audit.py:82`, `corpus_profiles.py`'s `"gate-verify-due"` | `test_gap_audit.py` correctly left untouched (field stays valid); `corpus_profiles.py` entry deleted (scenario no longer meaningful, not a compile fix) | Re-ran `pytest tests/test_gap_audit.py` — collects and passes |
| `CORE-REDESIGN-OWNERSHIP-INVENTORY...md`'s stale rows | `gate_canary.py` (missing), `effects_gates.py`/`cli.py` (over tolerance), `rules_attention.py` (stale prose) | All four fixed in Work item 10 (`1b76dfa9`) | Re-ran `pytest tests/test_core_characterization.py -k test_inventory` — 5/5 PASS |

**No new missed call site found.** I independently re-ran the same three-token sweep
(`coverage_gate`/`mutation_gate`/`gate_canary`) plus symbol-level greps for
`VerifyGate`/`GATE_VERIFY_RECORDED`/`drain_verify`/`_run_verify_probe`/`gate_verify_interval_days`/
`days_since_gate_verify` across the **whole repo** (not just `src/`/`tests/`/`tools/`) against the
final tree — every hit is either a kept, deliberate exception (the two fields, `Policy.mutation_gate`,
the six `test_mutation_gate_*` functions) or already-cited in LOG.md.

**Two extra test files edited beyond the literal `scope.touch` list**, both self-disclosed in
LOG.md/REPORT.md: `tests/test_gate_scaffold.py` (1 assertion: `asserts == ["tests-pass",
"changed-line-coverage"]` → `["tests-pass"]`) and `tests/test_onboarding_gate.py` (2 assertions:
`"nyxloom gate verify" in ...` → `"assay/" in ...`). Both are direct, single-assertion, mechanical
consequences of Work item 5's own already-instructed prose/behavior change — not new behavior, not
a product decision. `escalate_if` #1 is scoped to "non-test file"; these are test files whose own
assertions had to track a change the handoff explicitly ordered elsewhere. Reviewed the exact diffs:
neither introduces a judgment call beyond picking a substring that matches the new (also
handoff-specified) text. Accepted as in-scope mechanical continuation, not a violation.

## 2. Flat-shim / re-export blast radius

No compatibility shim, re-export, or `__getattr__` fallback was introduced anywhere in the diff
(confirmed: no `__getattr__` additions in `mutation_gate`→`mutants` transition, no `coverage_gate`
symbol re-exported from anywhere, no `gate verify` CLI alias). The retirement is a clean deletion,
not a deprecation shim — matches the product goal ("retires GA1/GA4 entirely, no replacement")
exactly rather than leaving a half-migrated compatibility layer that could mask future dependents.

## 3. Oracle re-verification (independent, from the frozen frontmatter, against the actual tree)

All nine re-run directly, not read off the implementer's claims:

```
O1: git ls-files | grep -E "coverage_gate|mutation_gate|gate_canary|gate_verify_cadence" -> empty; mutants.py present
O2: (see §5 — full independent gate run, PASS)
O3: `nyxloom gate --help` -> "usage: nyxloom gate [-h] {} ..." (empty choice set), exit 2
O4: schema True; grep -c assay-verdict config.py=1, STANDARD.md=1
O5: grep -c "OFFERED..."=0, "nyxloom gate verify"=0, "asserts=[tests-pass"=1
O6: decisions.md entry, 275 words, both facts + citation present
O7: P90 gone from handoffs/, present at archive/, first 15 lines name P98 + the specific reason
O8: 11/11 symbol-introspection checks PASS (VerifyGate/verify_gate/_run_verify_probe/drain_verify/
    GATE_VERIFY_RECORDED/rules_attention.gate_verify/rule_table entry all absent;
    Policy.gate_verify_interval_days/ReconcileInput.days_since_gate_verify both still present)
O9: onboarding_gate.py/gate_scaffold.py have zero "coverage_gate" hits; onboarding_gate.py zero
    "nyxloom gate verify"; config.py zero "cmd_gate_verify"
```

All PASS, matching `nyxloom-P98-REPORT.md`'s claims exactly.

## 4. Evasion probes — planted violations, confirmed caught, then reverted

Per the review protocol, actually broke things rather than reading the code and assuming:

- **Probe 1 (O2's "MUTATION-CHECKED" claim):** inserted `import nyxloom.mutation_gate` into
  `tests/test_mutants.py`. `pytest tests/test_mutants.py -q` → `ModuleNotFoundError: No module named
  'nyxloom.mutation_gate'`, collection aborted. Confirms O2's claim that a stray import of a deleted
  module is genuinely fatal, not asserted prose. Reverted; `git diff --stat` clean afterward.
- **Probe 2 (the central "kept fields" finding from the fix-verification rounds):** deleted
  `Policy.gate_verify_interval_days` from `config.py`. Result: O8's introspection script correctly
  flips to FAIL on that check, **and**, independently, `pytest tests/test_planner_differential.py`
  breaks **25 tests** (every synthetic/corpus differential scenario plus the divergence-classification
  tests) — because `tests/legacy_planner.py`'s frozen code reads
  `inp.cfg.policy.gate_verify_interval_days` unconditionally off the same `Policy` instance the live
  planner consumes. This is real, wide-blast-radius protection, not a theoretical byte-identity check
  alone — confirms the whole "keep these two fields for `legacy_planner.py`" design actually holds
  under a real regression, not just under O8's narrow script. Reverted; `git diff --stat` clean
  afterward, `test_planner_differential.py` green again.

I did not probe O3's "reproducing the same behavior under a different name" clause — it is
inherently a human-judgment oracle (not mechanically automatable), and O1 already closes the cheap
version of that attack (the file `gate_canary.py` a rename would need to import no longer exists).

## 5. Independent gate run (not the implementer's log)

Ran `./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-p98 tester-unified` myself from
this worktree, `docker update --cpus=3`'d within seconds of container start. Verdict read in a
**separate** step, from the artifact, not the run's own tail:

```
$ python3 -c "import json; d=json.load(open('.assay/verdict-tester-unified.json')); ..."
outcome: PASS
reason_code: None
exit_code: 0
commit: b7daa66fe53f9e4e221e4737f8a1288c448c612b
 claim: R0 PASS
 claim: R1 PASS
```

Commit matches the tip under review exactly. Container torn down automatically by `run-gate.py` on
completion (confirmed gone via `docker ps -a`). This independently reproduces `nyxloom-P98-REPORT.md`'s
"Run 4" result from scratch, on a separate invocation, not a re-read of their artifact.

## 6. Hollow-test check

- `tests/test_mutants.py` (new, 27 cases moved + 1 added): every case asserts a specific,
  deterministic transformation (operator, description string, and a substring of the mutated
  source) — none merely checks "doesn't raise." `test_falsy_swap_motivating_survivor` correctly
  stayed with the deleted `test_mutation_gate.py` (it also drives `mg._run_is_killed`, the retired
  judgment half) rather than being carried over hollow.
- `tests/test_gate_scaffold.py`/`tests/test_onboarding_gate.py` updates assert the **new** real
  behavior (`asserts == ["tests-pass"]`, `"assay/" in recommendation`) — not weakened to something
  that would pass on a broken implementation (e.g. not loosened to `assert offer.recommendation`
  alone).
- `tests/test_planning.py`'s `test_every_rule_names_a_real_contract_item` update
  (`{8}` → `{8, 16}` excluded) is a real, still-failing-if-wrong assertion: I confirmed by inspection
  that `covered == set(range(1,18)) - {8, 16}` would fail again if the `gate-verify` `RuleSpec` were
  still present (item 16 would be back in `covered`), so this isn't a loosened check that happens to
  pass — it actively re-verifies the removal.
- No coverage-evasion pragma anywhere in the diff (`grep -rn "pragma: no cover\|# nocov"` across the
  full diff — no hits), and the one real Assay R1 gap the implementer hit
  (`mutants.py:98` uncovered) was closed with a real behavioral test
  (`test_falsy_swap_ignores_a_non_literal_return_value`), not an exclusion.

## 7. Non-blocking observations (not blockers)

1. **`reference/STANDARD.md`'s "Validation methodology" intro** (unchanged by this diff, pre-existing
   text, line ~216): "...these are the hard-won practices that make one that genuinely does (learned
   building nyxloom's own gate + `gate verify`)." This is a fourth "gate verify" mention O5's oracle
   doesn't count (the literal string is `nyxloom's own gate + `gate verify`\`, not the contiguous
   `nyxloom gate verify`). It reads as historical/past-tense credit, the same category the handoff
   explicitly whitelists for the "gate verify v1 passed its own gate" anecdote a few lines below
   (Work item 7's own exception clause) — doesn't claim the tool currently exists. Optional polish
   only; does not violate any oracle or the product goal.
2. **`tests/test_mutants.py`** has two docstrings/comments with visible "thinking out loud" residue
   left over from authoring (`test_line_scoping`: "Actually: line 1 = ... Let me use
   target_lines={3}..."; `test_multiple_ops_on_one_line`: "Wait — since ast.unparse may not preserve
   token-level spacing..."). Harmless — the tests themselves are correct and non-hollow (§6) — but
   worth a trivial cleanup pass before this becomes the permanent home of these cases.

## 8. Reconciliation against LOG.md / REPORT.md

Read both only after completing §1-6 above. Every substantive claim reconciles with what I
independently found:

- The BLOCKED→repair episode (Round 1: correctly escalated the out-of-scope
  `CORE-REDESIGN-OWNERSHIP-INVENTORY...md` staleness as `escalate_if` #1 rather than improvising a
  fix; Round 2: executed Work item 10 exactly as the coordinator's repair specified) is corroborated
  by `git log` (commit `52bf54ea` "LOG + REPORT, BLOCKED on an out-of-scope inventory file" between
  the two rounds) and by the now-passing `test_inventory_*` suite.
- The Run 3 host-contention flake (`test_sequence_integrity_under_concurrency`,
  `sqlite3.OperationalError: database is locked`) is a plausible, correctly-diagnosed non-regression
  (nothing in this diff touches `storage.py`/`storage_sqlite.py`/`test_properties.py`, confirmed by
  `git diff --stat` for those paths being empty) — and my own independent Run 4-equivalent gate
  execution passed clean on the first attempt, consistent with it having been transient.
- One trivial inaccuracy, not a defect: REPORT.md's test-file-pruning section says `test_daemon.py`
  has "four comment mentions of the sibling deleted file's name" — the actual file has zero mentions
  of `coverage_gate`/`gate_canary` and the `mutation_gate` hits are exactly the six kept
  `test_mutation_gate_*` functions' own names/kwargs/docstrings, not a distinguishable set of four.
  Doesn't affect any conclusion — `test_daemon.py` is genuinely untouched and correctly so.
- Forbid-list integrity, independently re-confirmed via `git diff --stat` returning zero lines for
  each: `src/nyxloom/gate_runner.py`, `src/nyxloom/effects_merge.py`, `tests/legacy_planner.py`,
  `tests/test_remote_mutation_audit_tools.py`, `nyxloom-trove/nyxloom.toml`, `tests/test_daemon.py`.
  Two carve-time-only files present in the diff range but predating and untouched by the
  implementation phase (`nyxloom-trove/reports/ASSAY-NYXLOOM-REORIENTATION-2026-08-17.md`,
  `M3-ASSAY-CIU-PACKAGE-PLAN-2026-08-17.md`) — both created in the original carve commit `73887702`,
  confirmed via `git log 73887702..b7daa66f` returning no hits for either path.

## Verdict: ACCEPT

No product-level decision is needed from the coordinator on this review. Merge-ready as-is; the two
§7 items are optional cosmetic follow-ups, not conditions.
