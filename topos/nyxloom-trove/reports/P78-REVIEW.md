# P78-REVIEW — frontier review pass #2

Reviewer: Opus high, fresh session. Wave of 4 (P78/P83/P84/P85).
Date: 2026-07-13.

## Verdict

**Merged after one review-fix.** The extraction is well-shaped: one private
`_execute_gated` chain, four verbs supplying ordered gate closures, public
signatures untouched, `test_actions.py` (200) and `test_p72_kill_update.py` (51)
passing **unmodified** — the primary oracle, satisfied structurally (neither file
appears in the diff).

## The architecture-reconciliation decision (verified)

The pre-implementation round resolved contract 2 ("no verb gate may move to after
the audit-first write") against contract 3 ("byte-identical observable behavior")
by keeping P49's stale check **post**-pre-audit-write as a documented exception.

**The decision is correct, and for the right reason.** Contract 3 is the stronger
constraint: a stale refusal on `main` writes a `pre` record *and* a `post` record.
Moving the stale check ahead of the pre-audit write would have deleted one of
those two records — an observable change to the audit trail, which contract 3
forbids in the same breath ("every audit record field and its ordering"). The
handoff update is coherent: contract 1 now names two gate categories, contract 2
explains the placement and the reason, contract 6 restates it, and P80's handoff
was updated to tell the successor that the post-audit hook is P49-specific and
not a general-purpose escape hatch. Verified empirically: `setprop/STALE` still
produces exactly 2 audit records.

## Findings

### F1 — `execute_set_property`'s verb-gate refusals reported the wrong `kind` (CONFIRMED, fixed)

`flagged-by-pass-1: no`

`_execute_gated` is entered with an `initial_kind`, and every pre-audit refusal
is emitted as `_refusal(initial_kind, ...)`. `execute_set_property` passes
`property_name or "systemd-set-property"` as that initial kind — so with the
CLI's `property_name="memory.high"`, its unit/value/persistence refusals
reported `kind="memory.high"`.

On `main` those three refusals hardcode `_refusal("systemd-set-property", ...)`.
Only the *generic* P46 gates (admin/confirm/root/timeout/audit-path) use the
property name — a pre-existing quirk the extraction had to preserve, not
normalise.

`kind` is an `ExecuteResult` field surfaced by `result_to_jsonable()`, so this
was an observable change to CLI JSON output on three refusal paths — a contract 3
violation and, strictly, an `Escalate-if` (BLOCKED) condition.

**How it was found.** A differential refusal-taxonomy harness (`/tmp/p78-diff/`)
run against `main` and the branch: 52 scenarios covering every (verb x
gate-failure) pair, comparing all result fields *and* the full audit JSONL
records. 3 divergences, all `setprop`. After the fix: **0 divergences across 52
scenarios / 34 audit records**, with outcomes `{success: 9, refusal: 36,
runner_failure: 4, timeout: 2, stale: 1}` — i.e. the deep paths genuinely
execute; an all-refusal transcript would have proved nothing.

**Why the package's own oracle missed it.** Its `test_differential_verb_gate_taxonomy`
asserts `(outcome, audit_outcome, stderr)` — *exactly* the three fields the
handoff's Oracle 1 named ("assert the exact `outcome`, `audit_outcome` and
`stderr` string"). The implementer followed the brief precisely. The **carve**
under-specified relative to its own contract 3, which says "every audit record
field". This is the §12 deciding-log lesson one level up: *a review can only
check what the oracle made checkable* — and here the oracle was written by the
carver, not the agent.

**Fix.** `_GateRefusal` grows an optional `kind` override; the three
set-property verb gates declare it. Added
`test_set_property_refusals_report_the_same_kind_as_before_extraction` (7
parametrized cases pinning `kind` on every set-property refusal path).
Mutation-tested: 3 cases red against the unfixed chain.

### F2 — line-count oracle (Oracle 5) is met, but thinly (ACCEPTED)

`execute.py`: 1438 -> 1237 lines (net -201). The handoff expected "~600+ lines of
duplication to go". Duplication actually removed is ~390 lines (the net, plus the
~190-line shared chain that did not exist before), so the job is done; the
headline number is smaller than the carve predicted because each verb still owns
its gate closures — which is the correct shape, not a shortfall. Oracle 5 is
explicitly "not the goal". No action.

## Pass #1 overlap

SELFREVIEW findings: mechanical only (scope, dates, ASCII, gate commands). It did
not surface F1. **Substantive overlap: 0/1.** Consistent with every prior wave —
same-session review does not catch the overclaimed-contract class unless the
carve pre-named the trap (cf. P70). Running total supports keeping pass #1 as
triage, not promoting it.

## Gates (controller environment)

Environment: `/tmp/p79-venv` (Python 3.14.6; zstandard 0.25.0, textual 8.2.8,
pytest 8.4.2, mcp 1.28.1). `-p no:schemathesis` is required with `-W error`: the
ambient site-packages carry a schemathesis/jsonschema DeprecationWarning that
errors at plugin-import time, unrelated to topos.

```
pytest topos/tests/test_actions.py topos/tests/test_p72_kill_update.py -q -W error
  -> 251 passed          (200 + 51, both files UNMODIFIED by the diff)
pytest topos/tests/test_p78_action_kernel.py -q -W error
  -> 72 passed           (65 original + 7 added at review)
timeout 900 pytest topos/tests -q
  -> 1 failed, 1277 passed
     the 1 failure is test_zst_without_zstandard_exits_2, which the handoff
     predicts verbatim ("your run should show that one and no other") -- it is
     P82's repair, already merged to main, absent from this branch's older base.
py_compile / git diff --check -> clean
```

Post-merge validation from `main` is recorded in P78-LOG.md.
