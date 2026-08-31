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
