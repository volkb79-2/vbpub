---
kind: backlog-entry
schema_version: 1
id: NL-3
title: "L10 handoff-size thresholds are hardcoded constants, need a per-project nyxloom.toml override"
status: fixed
type: "feature"
severity: "medium"
provenance: "dstdns 2026-08-29, packages P137/P138 (nyxloom-trove/reviews/dstdns-P137-carve-review-r1.md, dstdns-P138-carve-review-r1.md)"
filed_date: "2026-08-29"
closed_date: "2026-09-03"
closed_reason: "nyxloom-P99, merged 49525ef9: adds optional [lint.l10] table to nyxloom.toml (warn_tokens/error_tokens override, both directions), fails loudly on malformed values at ProjectConfig.load time"
---

## Observed mechanism and reproduction

`_check_l10` in `src/nyxloom/lint.py:1078-1097` hardcodes the L10 handoff-size
thresholds as Python literals: `tokens > 12000` -> error, `tokens > 6000` ->
warning, where `tokens = len(full_text) // 4`. There is no config surface —
not `nyxloom.toml`, not a project-level lint config, not a CLI flag — to
change these numbers per project. The only way to tune them is editing the
tool's own source.

Reproduction, 2026-08-29 (dstdns operator session): two legitimately dense
handoffs (`dstdns-P137-admission-bound-fixes.md`, 11 oracles across a
carve-review repair that added a new oracle + widened an existing SQL fix
across five call sites; `dstdns-P138-persister-lineage-followups.md`, 7
oracles closing a prior code review's B1/B2/M1-M4 findings) both exceeded
12000 tokens purely from carve-review-mandated precision (exact `file:line`
citations, MUTATION-CHECKED clauses, negative-space enumeration) — content
density the program's own doctrine (`AUTHORING.md`, the oracle-rigor rules)
requires, not bloat. The fix applied this session was `git diff` against
`src/nyxloom/lint.py` directly (raising the constants to 10000/18000), which
is exactly the workaround this entry exists to make unnecessary: it changes
the threshold for every project consuming this nyxloom checkout, not just
the one that needed a different ceiling.

## Why nyxloom owns it

`nyxloom lint` is the shared handoff-authoring gate every registered project
runs before dispatch (`AUTHORING.md`'s lint step). The threshold is currently
a single global constant with no per-consumer override, so any project whose
packages are legitimately denser (more scope.touch files, more oracles, a
carve review's repair round adding findings) than whatever number was tuned
for a different project's typical package shape is stuck either living with
false-positive L10 errors on genuinely necessary content, or a source edit
that changes the number estate-wide.

## Proposed contract

Add an optional `[lint.l10]` table (or equivalent) to a project's
`nyxloom.toml`, read by `_check_l10` before falling back to the current
defaults (10000 warn / 18000 error, post this session's bump):

```toml
[lint.l10]
warn_tokens = 10000
error_tokens = 18000
```

Absence of the table/keys falls back to the tool-wide default — no project
is forced to add configuration it doesn't need. A project may raise its own
ceiling (a program with denser oracle conventions) or lower it (a program
that wants tighter handoffs) without touching nyxloom's own source.

## Oracles

- A project with `[lint.l10]` `error_tokens = 25000` in its `nyxloom.toml`:
  a handoff at 20000 tokens lints WARNING, not ERROR — proves the override
  is read, not just parsed.
- A project with no `[lint.l10]` table: unchanged current behavior (10000
  warn / 18000 error) — proves the fallback path is untouched.
- Controlled wrong implementation: hardcoding the override behind a feature
  flag that defaults off would pass the first oracle only when explicitly
  enabled in each test's fixture — the real fix must apply from a bare
  `nyxloom.toml` table with no flag.
- Malformed `[lint.l10]` (e.g. `warn_tokens > error_tokens`, or a negative
  number): FAIL LOUDLY (AGENTS.md §4.2a analogue for nyxloom) — never
  silently swap the values or silently ignore the malformed table and fall
  back without saying so.

## SPEC ownership

`nyxloom lint`'s rule set and its config surface — same section of nyxloom's
own SPEC/STANDARD doc that already owns L1-L13's definitions and any
existing per-project lint config precedent (check for one before assuming
none exists).
