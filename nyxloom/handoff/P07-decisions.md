# P07 — decisions-inbox integration

> Tier: haiku · Depends-on: none · Read first: handoff/STANDING.md,
> src/nyxloom/decisions.py (docstring = normative incl. entry format and
> heading regex), docs/ARCHITECTURE.md §8.

## Owned files
- `src/nyxloom/decisions.py`
- `tests/test_decisions.py`

## Sample inbox (use verbatim as a test constant; it mirrors live dstdns usage)
```markdown
# Decisions inbox

Preamble prose that parsers must ignore.

---

## D-001 · 2026-07-13 · gap-analysis session · DECIDED 2026-07-13

**Question:** Ratify the launch bar?

**Resume prompt:** "Discuss D-001: read the pointers, challenge the bar."

**Decision (user, 2026-07-13):** Ratified, tightened.

---

## D-002 · 2026-07-14 · reviewer session · OPEN

**Question:** Is the live-performance page launch scope?

**Why it matters:** sequencing.

**Resume prompt:** "Discuss D-002 in docs/DECISIONS-INBOX.md."

---

## D-003 · 2026-07-14 · carver · DISCUSSING

**Question:** Something being discussed; no resume prompt yet.
```

## Oracles
1. `parse_inbox` → 3 Decisions: ids/dates/raised_by/status exact
   ('DECIDED','OPEN','DISCUSSING'); D-001.decided_note contains
   'Ratified'; D-002.resume_prompt == the quoted sentence (quotes
   stripped); heading_line values correct (assert exact ints). A malformed
   heading ('## D-XX broken') added to the text is skipped without error
   and without affecting the others.
2. `open_ids(cfg)` on sample_project after writing the sample inbox →
   {'D-002','D-003'}; missing inbox file → set() (delete it).
3. `reconcile_decisions` with seen={} → [('DECISION_OPENED','D-002'),
   ('DECISION_OPENED','D-003')] (order by id; D-001 already DECIDED and
   never seen → NOT resolved-announced). With seen={'D-002':'OPEN'} and
   the file edited so D-002 is DECIDED → [('DECISION_RESOLVED','D-002')].
   Unchanged statuses → [] (idempotence).
4. `decide(cfg,'D-002','option b','auth-on stays','user')`:
   - heading becomes `## D-002 · 2026-07-14 · reviewer session · DECIDED 2026-07-15`
   - a line `**Decision (user, 2026-07-15):** option b — auth-on stays`
     appears within D-002's section (before the next `## ` or EOF)
   - EVERY other line of the file is byte-identical (assert via
     line-diff, not just 'in text')
   - decide on 'D-001' (already DECIDED) → DecisionError; on 'D-999' →
     DecisionError.
5. `discuss(cfg,'D-002')` returns a string starting `claude ` containing
   `--append-system-prompt` and the resume-prompt text and the inbox path;
   single quotes inside the prompt are shell-escaped ('"'"' pattern or
   shlex.quote — use shlex.quote); `discuss(cfg,'D-003')` → DecisionError
   (no resume prompt).

## Guidance
- Parse with a single pass over lines; track current entry; `**Key:**`
  captures take the remainder of the paragraph (until blank line).
- Dates in decide() come from types.utc_now().date().isoformat() — the
  test freezes nothing; assert against today computed the same way.
- Never reserialize the whole file from parsed data — operate on the
  original lines (byte-identity oracle 4 enforces this).
