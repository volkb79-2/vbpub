# ciu-P36 — CIU-69: WORKTREE_TABLE_KEYS gains `exec_targets`

**Backlog:** `KNOWN_ISSUES_TODO_BACKLOG.md` CIU-69 (filed from the v8 design
session, `docs/CIU-V8-TESTING-GATE-PROPOSAL.md` rev 2.0 §4.7 X23).
**Worktree:** `/workspaces/vbpub/.worktrees/ciu-P36-worktree-table-keys/ciu`
**Branch:** `fix/ciu-P36-worktree-table-keys` (based on `vbpub main`)

---

## Reading, before writing anything

Read `AGENTS.md` (estate-wide policies) and `ciu/README.md` in full, then the
CIU-69 backlog row in full (`KNOWN_ISSUES_TODO_BACKLOG.md:275`), then the live
source at `src/ciu/worktree.py`:

- `WORKTREE_TABLE_KEYS` at `worktree.py:4009` (before this fix):
  `frozenset({"max_concurrent_instances", "lease_ttl_hours"})`.
- `_validate_worktree_table` (`worktree.py:4012-4024`) — the closed-key
  check, called from both `resolve_max_concurrent_instances`
  (`worktree.py:4108`, via line 4128) and `resolve_lease_ttl_hours`
  (`worktree.py:4186`, via its own call).
- `resolve_exec_targets_config` (`worktree.py:3045-3063`) — the SEPARATE
  reader for `[ciu.worktree.exec_targets]`. It does **not** call
  `_validate_worktree_table` at all; it reads `worktree_cfg.get("exec_targets")`
  directly off the full rendered global config and hands the sub-table to
  `parse_exec_targets` for its own four-key-per-alias grammar (S16.7). This
  confirms the backlog's framing: `exec_targets` was always independently
  well-formed on its own, but got refused the moment a budget/lease reader
  saw it sitting next to `max_concurrent_instances`/`lease_ttl_hours` in the
  same table.
- Confirmed the bug mechanically: `_validate_worktree_table` computes
  `unknown = set(raw) - set(WORKTREE_TABLE_KEYS)` over the WHOLE table, so
  any table containing `exec_targets` plus either other key fails before
  either resolver returns a value.

Grepped `tests/` for `WORKTREE_TABLE_KEYS` and `unknown key(s)`: the closed
set is exercised in `tests/tests/test_ciu_worktree_lease.py`, class
`TestLeaseTtlConfig` (S16.9 package, ciu-P26). Found the pre-existing
`test_the_table_key_set_is_closed_and_now_holds_two_keys` asserting
`WORKTREE_TABLE_KEYS == {"max_concurrent_instances", "lease_ttl_hours"}` —
this would fail the moment `exec_targets` was added, so it needed updating in
the same change, not left to break separately.

Checked `docs/SPEC.md` S16.3 (the task's one authorized doc-check) for a
literal enumeration of the closed key set: it does not enumerate the keys by
name in prose — only "An unknown `[ciu.worktree]` key ... fails loudly"
(generic). No SPEC.md change needed.

## Out-of-scope finding (not touched, per the task's explicit narrow scope)

`docs/CONFIG.md`'s `[ciu.worktree]` reference table (~line 205-229) lists
only `max_concurrent_instances`/`lease_ttl_hours` and states "Both keys share
ONE closed table" — this prose is now stale (the table has three key
families, not two) and does not mention `exec_targets` as living in the same
namespace at all. The task's Scope section named only `src/ciu/worktree.py`,
the test file, and a conditional SPEC.md check, and directed me to stop and
record rather than touch a third file unasked — so I did not edit
`docs/CONFIG.md`. Flagging this here as a real doc-drift a follow-up package
should close (AGENTS.md's "user-facing docs are part of the change" rule
would otherwise apply).

## Fix

`src/ciu/worktree.py`: added `"exec_targets"` to `WORKTREE_TABLE_KEYS`, with
a comment explaining that its own per-alias contents are validated
separately and only its presence as a top-level key is being accepted here.

## Test

`tests/tests/test_ciu_worktree_lease.py`, class `TestLeaseTtlConfig`:

- Renamed/updated `test_the_table_key_set_is_closed_and_now_holds_two_keys`
  -> `test_the_table_key_set_is_closed_and_now_holds_three_keys`, expected
  set now includes `"exec_targets"`.
- Added `test_all_three_families_coexist_in_one_table`: builds ONE
  `[ciu.worktree]`-shaped dict declaring `max_concurrent_instances`,
  `lease_ttl_hours`, AND `exec_targets.tester` together, then asserts:
  - `resolve_max_concurrent_instances(worktree_table) == 2`
  - `resolve_lease_ttl_hours(worktree_table) == 24.0`
  - `resolve_exec_targets_config({"ciu": {"worktree": worktree_table}})`
    returns the parsed `tester` target with the declared
    stack/service/workdir.

## Controlled-wrong-implementation sanity check (manual, not left as a second test)

1. Backed up `src/ciu/worktree.py` to the scratchpad.
2. Temporarily reverted `WORKTREE_TABLE_KEYS` to the original two-key
   frozenset.
3. Ran `python3 -m pytest tests/tests/test_ciu_worktree_lease.py -k
   test_all_three_families_coexist_in_one_table -q`: **FAILED**, raising
   `ciu.worktree.WorktreeError: [S16.3] unknown key(s) in [ciu.worktree]:
   exec_targets` at the `resolve_max_concurrent_instances(worktree_table)`
   assertion — exactly the message CIU-69 names as the controlled wrong
   implementation's expected failure.
4. Restored `WORKTREE_TABLE_KEYS` to the fixed three-key frozenset;
   `diff` against the pre-experiment backup confirmed byte-identical
   restoration.
5. Re-ran the same targeted test plus the full lease/worktree test modules:
   all green (see REPORT for full local-run counts and the real gate
   verdict).

## Commits

1. `43e5f4d1b54ec8c3f5d332252907bc22e390d0fd` — `fix(ciu): WORKTREE_TABLE_KEYS
   gains exec_targets (CIU-69)` — `src/ciu/worktree.py` +
   `tests/tests/test_ciu_worktree_lease.py`.
2. (this LOG file, plus the REPORT) — committed separately, hash recorded in
   REPORT.

---

## Addendum — CIU-76 folded into this package (coordinator directive, 2026-08-31)

The coordinator confirmed and filed two findings surfaced by this package's
CIU-69 gate run as `CIU-76` (`apply_lease` has no `now:` override) and
`CIU-77` (vendored self-test judge 3 majors behind), and separately fixed the
pin bug this package's REPORT flagged (`run-gate.toml`
`pins.assay.version` 2.2.0 -> 2.3.0, `b8102bc2`). New instruction: fold
CIU-76 into this package rather than leaving it for a fresh agent, since the
lease code was already loaded.

### Rebase onto current main first

Branch predated both `b8102bc2` (pin fix) and `858766d1` (CIU-76/77 filing).
Rebased with `git rebase -i main`, dropping the branch's own now-moot
temporary pin-patch-and-revert pair (`3e3ecb08`/`0239812b`) via a
`GIT_SEQUENCE_EDITOR` sed script that removed their `pick` lines — git itself
reported the patch as "skipped previously applied" (patch-id-equivalent to
`b8102bc2`, already upstream), confirming it was genuinely redundant, not
silently dropping real content. New hashes after rebase: `e68ee748` (the
CIU-69 fix+test), `3c842406` (LOG), `a0947dc2` (REPORT). Confirmed
`git diff main..HEAD -- run-gate.toml` is empty and the local test suite
(222 tests, the two lease/worktree modules) still green post-rebase.

### CIU-76 fix

Read the full filed entry (`KNOWN_ISSUES_TODO_BACKLOG.md` CIU-76 row) and
`apply_lease`/`acquire_lease`/`make_lease_perpetual` in
`src/ciu/worktree.py`. Added `now: datetime | None = None` to
`apply_lease`'s signature, threaded through to both the
`acquire_lease(...)` and `make_lease_perpetual(...)` calls (the `--release`
branch is untouched — releasing is not time-based).

Grepped every `apply_lease(` call site under `tests/` (18 total: 14 in
`test_ciu_worktree_lease.py`, 4 in `test_ciu_worktree_reap.py`) for the same
latent fragility. Only one — `test_re_expiring_after_an_extend_becomes_lease_expired_again`
— actually mixes a real-time `apply_lease` call with a frozen-`NOW`
checkpoint (`test_ciu_worktree_reap.py`'s local `survey()` helper defaults
`now=NOW`, so several other calls in that class ALSO check against the
frozen fixture, but their math stays safely one-directional relative to it
regardless of the real calendar date — verified by hand for each: see
REPORT for the per-test reasoning). Fixed only that one test, threading
`now=NOW` through its `apply_lease(...)` call.

Verified determinism two ways: (1) the 4-test class passes as a normal run;
(2) re-ran the single fixed test with `worktree._utc_now` monkeypatched to
raise `AssertionError` if called at all — it still passed, proving neither
the fixed `apply_lease` call nor anything else in that test's path consults
the real clock once `now=` is supplied everywhere it matters.

Checked `docs/SPEC.md` S16.9 for an `apply_lease` signature/determinism
contract to update: none exists — S16.9's prose describes the `ciu worktree
lease` CLI verb's OBSERVABLE behavior (real wall-clock time by default,
unchanged), not the internal Python function's signature. No SPEC.md change.

Full local suite (`PYTHONPATH=src python3 -m pytest tests -q --dist loadfile
-n auto`, no `PYTHONDONTWRITEBYTECODE` override): **3262 passed**, zero
failures — including the previously-fragile lease-reap test, now
deterministic.

### Commit

`d69d7c3db5398856fb677faf6fa2bb31af26057b` — `fix(ciu): apply_lease gains
now: override, fixes clock-coincidence test (CIU-76)` —
`src/ciu/worktree.py` + `tests/tests/test_ciu_worktree_reap.py`.
