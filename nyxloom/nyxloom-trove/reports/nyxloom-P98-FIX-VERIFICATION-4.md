# nyxloom-P98 — fix-verification round 4 (confirm exactly three stale rows + note convention)

**Repaired handoff:** frozen at `580ab61c` (`input_revision: "fca2d122"`). **Method:** re-ran the
identical full-table recomputation from round 3 (same regex, same tolerance formula as
`test_inventory_sizes_are_within_the_declared_tolerance`) over the current tree, independently,
rather than only re-checking the two rows the coordinator added.

## 1. Full re-scan: exactly three problem rows, nothing new

Parsed all 92 rows in `CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md` and checked each against
the real tree:

```
MISSING: src/nyxloom/gate_canary.py   recorded=402
STALE:   src/nyxloom/effects_gates.py recorded=473   actual=354  diff=119 tol=40
STALE:   src/nyxloom/cli.py           recorded=2,469 actual=2220 diff=249 tol=222
```

No fourth row exceeds its tolerance. `src/nyxloom/rules_attention.py` (recorded 118, actual 83,
diff 35, tolerance 40) is confirmed still *inside* tolerance — consistent with Work item 10's own
framing ("inside its own tolerance floor today"). Its inclusion in Work item 10 is for the
already-stale "gate-verify cadence" prose (Work item 3's rules_attention.py edit has already landed
— `git log`/`wc -l` confirm 83 lines, no `gate_verify` function), not because it fails the
mechanical size check. This matches the handoff's own characterization exactly: three rows to
re-measure/fix (`gate_canary.py` removed, `effects_gates.py` and `cli.py` re-measured because they
already exceed tolerance), plus `rules_attention.py` folded in for prose accuracy while already
editing the same document. No other row in the table (including every `daemon.py`, `reconcile.py`,
`planning.py`, `types.py`, `config.py` row this package also touches) shows any drift approaching
its tolerance band — all comfortably within it, confirmed by the same full scan.

Minor, non-blocking wording note: Work item 10 says to re-measure "on the tree AFTER Work items 2,
3, and 5's edits land." Work item 5 (onboarding_gate.py/gate_scaffold.py) doesn't touch any of
`effects_gates.py`/`cli.py`/`rules_attention.py` — the relevant edits are Work items 2 and 3. This
doesn't create any actual ambiguity or wrong measurement (the implementer re-measures against the
final tree after all package edits land regardless of which item is credited), so it isn't
blocking — just an attribution slip worth a one-line cleanup if convenient.

## 2. The "Re-measured" note — one consolidated note is correct, and Work item 10 already says so

The document's own precedent settles this directly. Its two existing header notes are both single,
consolidated entries per review event, not one note per row:

- "Re-measured 2026-08-03 (CR-16 review) for `doctor.py`, `notify.py`, and `watchdog.py` — the
  three surfaces CR-16 ... moved past their size tolerance." — one note, three files named inline.
- "Re-measured 2026-08-03 (CR-15 review) for the three rows CR-15 moved past the size tolerance,
  plus the one module it ADDED..." — one note covering multiple rows, naming the one that needed
  explanation (`control_auth.py`) inline.

Both precedents are organized per *editing pass*, not per row — exactly the convention Work item
10 already follows: "Add a short 'Re-measured 2026-09-02 (nyxloom-P98)' note ... explaining all
three changes" is singular ("a short note") and explicitly scoped to cover all three files in one
place, matching the CR-16 note's shape closely (one note naming multiple files). No change needed;
an implementer reading this instruction would not reasonably produce three disconnected notes, and
the wording does not invite that misreading.

## Verdict: READY

Independent full re-scan confirms exactly the three rows the current freeze already lists (plus
`rules_attention.py`'s already-acknowledged sub-threshold prose fix) — no fourth stale row exists
anywhere in the document. The "Re-measured" note instruction already matches the document's own
established one-note-per-pass convention and needs no change. No new or unresolved issues found.
