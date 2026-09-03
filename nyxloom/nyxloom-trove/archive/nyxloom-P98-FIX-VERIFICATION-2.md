# nyxloom-P98 — fix-verification round 2 (legacy_planner.py three-way bind)

**Repaired handoff:** frozen at `6dffb2ed` (`input_revision: "7aeb39ff"`). **Trigger:** the first
implementer dispatch correctly reported BLOCKED on a real bind neither review round caught:
`tests/legacy_planner.py` (forbidden to edit, byte-identity self-checked) reads
`Policy.gate_verify_interval_days`/`ReconcileInput.days_since_gate_verify` unconditionally off the
same production instances the live planner consumes. **Method:** ran the actual byte-identity test
and independent greps against the real tree rather than trusting the diff/prose.

## 1. legacy_planner.py's frozen nature and field reads — confirmed exactly as described

Ran `PYTHONPATH=src python3 -m pytest tests/test_planner_differential.py::test_legacy_baseline_is_the_committed_branch_point -q`
directly: **passes**, independently confirming the file is currently byte-identical (modulo its two
declared import rewrites) to `052857ae`'s `reconcile.py`. Read `tests/legacy_planner.py`'s header
(matches the handoff's description verbatim) and its cadence block (lines 2122-2130):
```
gate_verify_interval = inp.cfg.policy.gate_verify_interval_days
if gate_verify_interval > 0:
    ...
    gv_age = inp.days_since_gate_verify
```
Both reads are unconditional (no `hasattr`/`getattr`-with-default guard) against `inp` — the same
`ReconcileInput`/`ProjectConfig` object the differential harness feeds to both planners (per the
file's own docstring: "the same input object drives both planners"). Deleting either field from the
production dataclass would raise `AttributeError` inside this forbidden-to-edit file. Confirmed
real, not overstated.

## 2. No dangling "remove this kwarg" instructions remain

Grepped both field names across every test file scope.touch could plausibly still mis-instruct:
`test_effects.py`, `test_invariants.py`, `effect_differential.py`, `test_cli.py`, `test_daemon.py`
— **zero hits** in any (their entries were already scoped to `VerifyGate`/`GATE_VERIFY_RECORDED`/
`drain_verify` only, never these two fields, so no rewording was needed or missed there).
`test_planning.py`, `planner_corpus.py`, `test_planner_differential.py` — zero direct hits (their
concern is the `RuleSpec`/rule_table wiring, unrelated). Only two real hits remain: `corpus_profiles.py`'s
`"gate-verify-due"` tuple (correctly instructed for deletion as a scenario no longer meaningful, not
a compile fix) and `test_gap_audit.py`'s `days_since_gate_verify=100.0` (correctly left as
verify-only). The repair's claim holds.

## 3. Stub-and-leave-wired attack re-run against the revised O8 — no new false-PASS path

Checked whether the "legacy compatibility" exception could be stretched to hide other dead GA4
state. It cannot, on the actual evidence: `legacy_planner.py` defines its **own local** `VerifyGate`/
`Action` dataclasses (a verbatim textual copy from `052857ae`, not an import of `reconcile.VerifyGate`)
— it never touches production's `VerifyGate`, `GateEffector`, or `EventType.GATE_VERIFY_RECORDED`.
The only cross-module coupling is the two data fields read off the *shared instance*. O8's
absence-checks for `reconcile.VerifyGate`, `GateEffector.verify_gate`/`_run_verify_probe`/
`drain_verify`, `EventType.GATE_VERIFY_RECORDED`, `rules_attention.gate_verify`, and the
`rule_table()` entry are **unchanged** from the prior round — still required absent, still no
textual hook in Scope/forbid to extend the exception ("This is not the same case as
`Policy.mutation_gate` above" explicitly closes that door). The inverted presence-check is
correctly narrow: exactly two named fields, nothing broader. No new attack surface opened.

## 4. Corpus-fixture claim — independently confirmed

`grep -c "gate_verify_interval_days\|days_since_gate_verify" tests/fixtures/planner_corpus_v1.json`
→ `0`. File loads as valid JSON (a 4-key dict). Zero references to either field name anywhere in the
real historical corpus, confirming no `KNOWN_DIVERGENCES` entry is needed — only the synthetic,
now-deleted `"gate-verify-due"` profile ever exercised this path.

## 5. `tests/test_gap_audit.py` scope status — consistent, and safer than described

Note: the coordinator's message says this file was "dropped ... from the edit list entirely," but
the actual diff (`d5971a79` → `7aeb39ff`) shows it was **not** dropped — it stays in `scope.touch`,
re-commented as "verify-only, no edit expected ... listed because O2 requires confirming it still
collects and passes" (the same pattern already used for `test_remote_mutation_audit_tools.py` and
`planner_corpus.py`). This is the better outcome, not a discrepancy to fix: it is both listed in
`scope.touch` and named in O2's collection list, so there is no lint-invisible gap.

## Verdict: READY

All five checks independently re-verified against the actual tree, not the diff. No new or
still-unresolved issues found.
