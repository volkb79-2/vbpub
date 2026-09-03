# nyxloom-P98 — fix-verification pass against nyxloom-P98-CARVE-REVIEW.md

**Repaired handoff:** frozen at `6dae0bb3` (`input_revision: "122175bd"`, the repair's own prior
commit). **Method:** re-verified each B1-B4 finding, the §4 scope table, and the O1-O9 false-PASS
attacks against the actual tree (no code changed between the reviewed and repaired freeze — only
the handoff text; confirmed via `git show 122175bd --stat`, one file). Did not re-derive new
findings outside what the repair itself introduces.

## B1-B4 — all resolved

- **B1 (STANDARD.md):** Work item 7 now sequences all three prior occurrences (~192, ~210-225,
  ~266) plus the "OFFERED, not mandated" paragraph, with an explicit keep/delete split for the
  ~210-225 paragraph (keep the rigor-declaration sentence + NL-4 mirror; delete the GA1-proving
  sentences). Verified `doctor.py:801` independently calls `transport_check.probe_default()` —
  the occurrence-1 rewrite ("`nyxloom doctor` fails closed...") is factually accurate, not a new
  fabrication. O5's third grep (`asserts=\[tests-pass` must survive) closes the original "delete
  the whole paragraph" attack. Resolved.
- **B2 (Policy vs GateDef):** verified `gate_verify_interval_days` (config.py:173) and
  `mutation_gate` (config.py:198) are both `Policy` fields (class spans 112-265); scope.touch,
  Work item 3, and Scope/forbid now all correctly say `Policy`. Resolved, all three instances.
- **B3 (stray `nyxloom/` path prefix):** fixed as a side effect — scope.touch and Context-to-read
  now use bare repo-relative paths (`reference/STANDARD.md`, `assay.toml`, etc.) throughout.
- **B4 (item-16/17 numbering):** Work item 3 now makes an explicit decision (leave the gap, do not
  renumber 17→16) and correctly identifies item 17's cross-reference as by field name, not number
  (`reconcile.py:335`, verified). An `escalate_if` entry backstops it. Resolved.

## §4 scope-gap table — all seven entries now present and correctly instructed

`planning.py` (RuleSpec removal, correctly described), `tools/remote_mutation_audit.py` (import
retarget to new `mutants.py`), `tests/test_snapshot_faults.py`, `tests/test_gap_audit.py`,
`tests/corpus_profiles.py` + its two verify-only consumers, and `onboarding_gate.py`'s second
mention — all addressed with instructions matching the actual code. Independently re-verified the
load-bearing extraction claim: `Mutant` (lines 73-80) and `generate_mutants` (96-276) in
`mutation_gate.py` have zero calls into `evaluate`/`_fanout_safe`/`_run_is_killed*`/
`_resolve_added_lines` (277-689) and zero references to `coverage_gate`/`gate_runner` — the split
is clean, matching Context item 5 and the escalate_if re-verification clause.

## False-PASS attacks — closed except one

O1, O3, O7, O9 fully close their original attacks. O6 substantially raises the bar (150-word floor)
though "reasoned vs. padded" still isn't mechanically checkable — soft residual, not blocking.
O4+O5 individually close the "delete-the-paragraph" and "schema-only" attacks, but combined they
have a minor gap: O4's `grep -c "assay-verdict" config.py`/`STANDARD.md` checks are location-blind,
so a stray unrelated mention would technically satisfy them without the real prose mirror — a
contrived, not "convenient," attack; not blocking.

**One genuine, unresolved gap: O8 checks the wrong class for `days_since_gate_verify`.** O8's
observable asserts `[f.name for f in dataclasses.fields(config.Policy)]` excludes both
`gate_verify_interval_days` **and** `days_since_gate_verify` — but `days_since_gate_verify` was
never a `Policy` field; it's `reconcile.ReconcileInput`'s field (verified, `reconcile.py:920`).
Checking it against `Policy` is vacuously true regardless of whether `ReconcileInput` was actually
fixed. Re-running the "stub it and leave it wired" attack narrowly: remove `VerifyGate`,
`GateEffector`'s three methods, `EventType.GATE_VERIFY_RECORDED`, `Policy.gate_verify_interval_days`,
and the `rule_table()` entry exactly as specified (O8 now correctly fails on all five if any is
left) — **but leave `ReconcileInput.days_since_gate_verify` declared** (its callers just stop
passing the kwarg, which is silently valid against the still-present field with its default). This
passes O8 in full, passes O2 (no test breaks — the field simply goes unused), and is never caught
by anything else. Fix: O8's check should target `dataclasses.fields(reconcile.ReconcileInput)` for
`days_since_gate_verify`, separately from the `Policy` check for `gate_verify_interval_days`.

Minor, non-blocking, unrelated to the above: `config.py`'s `GateDef.asserts` docstring (lines 66-70,
the same block Work item 6 edits for the NL-4 mirror) still says "`nyxloom gate verify`
cross-checks it against its own observed verdict ... see `cli.cmd_gate_verify`" — both name a
function this package deletes. Work item 6 only touches the enum-list line (64-65); no oracle
checks this docstring's other sentences.

## Verdict: NOT READY

Every B1-B4 finding and every scope-table gap from the original review is genuinely resolved, and
five of the six GA4 symbols the "stub it and leave it wired" attack relied on are now correctly
guarded by O8. One narrow, precisely-located gap remains: O8's `days_since_gate_verify` check
targets `config.Policy` instead of `reconcile.ReconcileInput`, leaving that one field's removal
completely unverified. This is a single-line oracle fix, not a re-sweep — recommend correcting O8
and re-freezing; no other repair is needed.
