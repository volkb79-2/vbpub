# nyxloom-P100 — fix-verification round 4 (ownership-inventory reverse dependency)

**Repaired handoff:** frozen at `45732f58` (`input_revision: "3519a73f"`), added after a fresh
implementer completed Work items 1-4 and correctly reported BLOCKED per `escalate_if` #1 rather than
improvising. Implementer's own commits (`453950d8`..`00a2482a`) are untouched below the repair.
**Method:** independent full re-scan of the inventory doc (same script used for nyxloom-P98's
analogous finding), not a re-read of the implementer's or coordinator's claims.

## 1. Confirmed independently: only `src/nyxloom/lint.py` has a row; `test_lint.py`/`AUTHORING.md` do not

`grep -n "lint\.py\|test_lint\|AUTHORING" CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md` returns
exactly two hits: line 119 (`src/nyxloom/lint.py`'s own row) and line 151 (`doc_lifecycle.py`'s row,
which merely *mentions* "lint.py" in its prose as a consumer of `is_archived` — not a row for
`lint.py` itself). Separate greps for `conftest\.py` and `handoff-frontmatter.schema` return nothing.
No row exists for `tests/test_lint.py` or `reference/AUTHORING.md` anywhere in the 91-row document.
**Confirmed independently.**

## 2. Work item 5 + O7 — sufficient, nothing else affected

Ran the same full-table tolerance recomputation used in the nyxloom-P98 review (identical regex and
formula to `test_inventory_sizes_are_within_the_declared_tolerance`) over all 91 rows against the
current tree:

```
MISSING: []
STALE:   [('src/nyxloom/lint.py', '1,112', 1262, 150, 126)]
```

Exactly one problem row, exactly the one Work item 5 targets. The math matches the BLOCKED LOG's own
figures precisely (recorded 1,112, actual 1262, diff 150, tolerance `max(40, int(1262*0.10))=126`).
No other row in the document is affected — Work items 1-4 touched `reference/AUTHORING.md` (not
inventory-tracked), `src/nyxloom/lint.py` (the one stale row), `tests/test_lint.py` (not tracked),
and `tests/conftest.py`/the schema (both verify-only, confirmed unedited by the implementer's actual
commits). O7's two named tests are the correct, minimal pair (path-existence is unaffected since
`lint.py` still exists; only the size-tolerance test can fail from this specific change). Work item
5's instruction to re-measure with `wc -l` rather than hardcode, and to touch only `lint.py`'s row,
is unambiguous and matches nyxloom-P98's own precedent for this exact document. **Sufficient.**

## 3. Diff sanity check — nothing else drifted

```
git diff 898ee8a2..45732f58 -- nyxloom-trove/handoffs/nyxloom-P100-tier-routes-toml-validation.md
```
shows exactly: the `input_revision` bump, two new `scope.touch` entries
(`tests/test_core_characterization.py` verify-only, and the inventory doc with a detailed rationale
paragraph), the new O7 oracle block, and the new Work item 5 block appended after Work item 4. No
existing oracle (O1-O6), no `escalate_if` entry, no `Scope/forbid` entry, and no earlier Work item
was altered, reworded, or renumbered. Independently confirmed `wc -l src/nyxloom/lint.py` = 1262,
matching both the BLOCKED LOG's figure and the repair's stated re-measurement target.

## Verdict: READY

All three checks confirmed independently against the actual tree, not the coordinator's or
implementer's prose. The repair is minimal, correctly scoped, and sufficient — no other row in the
inventory document is affected by this package's edits, and no unrelated part of the handoff shifted.
