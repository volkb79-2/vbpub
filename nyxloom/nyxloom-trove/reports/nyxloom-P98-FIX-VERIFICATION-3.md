# nyxloom-P98 — fix-verification round 3 (ownership-inventory doc, real gate finding)

**Repaired handoff:** frozen at `b7467142` (`input_revision: "5473063c"`), Work item 10 added
after the second implementer's real gate run correctly reported BLOCKED. **Method:** ran the
actual `tests/test_core_characterization.py::test_inventory_*` tests directly against the current
tree (13 real commits already landed) and independently recomputed the size-tolerance check over
every row in the document, rather than trusting the two rows the finding named.

## 1. Doc is genuinely NOT frozen — editing is the correct fix

The document's own text settles this unambiguously: "Purpose: planning input for the core-redesign
reviews. This is deliberately not a product gate and sets no size limit," and its "Mechanical
contract" section: "This document is checked by tests, so a reader editing it knows what fails and
why... Ordinary edits do not churn this file; real drift fails visibly and is **re-measured, not
silenced**." `test_inventory_paths_all_exist`'s own assertion message — "inventory names paths that
no longer exist" — fails *because* a stale row was left behind, i.e. the test's entire design
assumes the document tracks current reality and gets updated. This is the structural opposite of
`tests/legacy_planner.py`'s "DO NOT EDIT" contract. Confirmed by directly running the test: it
currently fails with exactly `AssertionError: inventory names paths that no longer exist:
['src/nyxloom/gate_canary.py']` — editing this file to remove that row is unambiguously the fix,
not a bind.

## 2. A real, currently-failing row Work item 10 does not cover: `src/nyxloom/cli.py`

Independently recomputed the size-tolerance check over **every** row in the document (same regex,
same tolerance formula as the test), rather than only the two rows named in the finding:

```
STALE src/nyxloom/effects_gates.py recorded=473  actual=354  diff=119 tol=40   (already in Work item 10)
STALE src/nyxloom/cli.py            recorded=2,469 actual=2220 diff=249 tol=222  (NOT in Work item 10)
MISSING src/nyxloom/gate_canary.py  recorded=402                                 (already in Work item 10)
```

`cli.py`'s row is stale for the same reason as `effects_gates.py`'s: Work item 2's already-landed
CLI-surface removal (`git log` confirms commit `74a3664f`, "remove the GA1 gate-verify CLI surface")
shrank it from the recorded 2,469 lines to a real, directly-verified 2,220 (`wc -l` matches exactly).
The drift (249 lines) exceeds the row's own tolerance (222 = 10% of 2,220) by 27 lines — this is not
a near-miss rounding artifact, it is a real violation of `test_inventory_sizes_are_within_the_declared_tolerance`,
the exact same oracle O2 names for `effects_gates.py`. Work item 10 as currently written only
touches the `gate_canary.py` row and the `effects_gates.py` row; it says nothing about `cli.py`.
**As written, resuming the implementer to do exactly Work item 10 and re-run the gate will hit the
same oracle failure a third time**, on a row the current repair round never looked at.

Secondary, non-blocking: `src/nyxloom/rules_attention.py`'s row (line 82) describes the module's
responsibility as including "the gate-verify cadence that is deliberately outside the carve mutex"
— stale prose, since Work item 3 already removed that rule (commit `c105767a`; `wc -l` confirms the
file is now 83 lines, not the recorded 118, and `grep` for `gate_verify` in it returns nothing).
This one does **not** fail any oracle: the drift (35 lines) is inside the 40-line floor tolerance,
and no oracle checks prose accuracy — only path existence and size tolerance. It is the same class
of staleness Work item 10 is explicitly being carved to close for `effects_gates.py`, just below
the mechanical detection threshold. Not blocking on its own, but worth folding into the same edit
for consistency (and to avoid a plausible "round 5" repeat of this exact pattern).

## 3. Re-measure vs. hardcode, and the other two named tests

Re-measuring (rather than hardcoding a predicted number) is the right call: the margin above shows
there is no slack to spare — `cli.py`'s actual drift (249) exceeds tolerance (222) by only 27 lines,
so a carver-predicted number typed into the handoff ahead of the implementer's real, byte-exact diff
would be exactly the kind of fragile guess that reintroduces this same failure mode.

Ran `test_inventory_covers_the_whole_control_plane_import_closure` and
`test_inventory_declares_test_ownership_for_every_rewritten_surface` directly at the current tree
state: **both pass**, and neither has a matching trap for this package. The closure test only flags
a module the control plane **gained** without a declared owner (`_control_plane_closure() - listed`)
— it never notices a row whose module **left** the closure, which is why the stale `gate_canary.py`
row doesn't fail it even though `daemon.py` no longer imports that module. The test-ownership test
only applies to rows tagged with a CR-05/06/07 package **and** an existing `tests/test_<module>.py`
file; verified directly that `tests/test_effects_gates.py` and `tests/test_rules_attention.py` do
not exist (so both rows are exempt regardless of tagging), and `cli.py`'s row is tagged
CR-01/04/14/15, none of which are rewrite packages, so it is exempt from this test too — its problem
is purely the size-tolerance check above.

## Verdict: NOT READY

One genuinely new, independently-verified, currently-reproducible gap: Work item 10 must also
re-measure and update `src/nyxloom/cli.py`'s row (2,469 → real current count at execution time,
already 2,220 as of this tree) — a third stale row in the same document that the current repair
round's sweep of "outside `src/`/`tests/`/`tools/`" scope still missed, this time within its own
declared fix target. This is a single additional row-edit, not a re-sweep: add `cli.py`'s row to
Work item 10's instruction and to O2's/the inventory doc's re-measurement, the same way
`effects_gates.py`'s row is already handled. Optionally fold in `rules_attention.py`'s stale
"gate-verify cadence" prose at the same time (non-blocking, but same root cause).
