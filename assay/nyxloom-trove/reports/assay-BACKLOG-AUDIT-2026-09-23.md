# assay backlog audit — 2026-09-23

**Status: IN PROGRESS.** This report is committed early and updated
incrementally per the audit's own working method (durable progress rule).
Do not treat an entry absent from the table below as unaudited-forever — it
means the corresponding pass had not landed yet at this commit; check the
git history of this file for the latest version, or the companion
CONTINUATION file if one exists alongside it.

## Method

1. Schema check first. `assay/nyxloom-trove/4-backlog.md` is a nyxloom
   "direction spine" document (`kind: backlog`, `schema_version: 1`,
   validated against `nyxloom/src/nyxloom/schemas/spine-backlog.schema.json`
   — NOT the separate "managed backlog entries" system in
   `nyxloom/src/nyxloom/backlog_entries.py`, which is a different,
   one-file-per-entry mechanism this project does not use). The frontmatter
   `items[]` schema is closed (`additionalProperties: false`) and allows
   only `id, title, type, component, context_estimate, folds_into` — **there
   is no status field in the schema**, and `type` is a closed enum
   (`feature` or `bugfix` — NOT `bug`, `bugfix` is the correct spelling).
   Confirmed by loading the schema with `jsonschema.Draft202012Validator`
   against the file's real frontmatter (via `nyxloom/src/nyxloom` imported
   directly, not simulated): the file **as it stood before this audit**
   already had 9 of its 20 listed items failing schema validation, all on
   `type: bug` instead of `type: bugfix` — a pre-existing defect, not
   something this audit introduced. Per the operator's brief, status is
   therefore recorded in the BODY, directly under each `## Bnnn` heading, as
   a normalized `**Status: VERDICT (date/version) — evidence.**` line, never
   as an invented frontmatter key.
2. Per-entry verdicts are evidence-derived: `assay/CHANGES.md`, `git log`/
   `git show`/`git diff` against `main` and named branches, `assay/nyxloom-trove/decisions.md`
   (A-nnn rows), reports under `assay/nyxloom-trove/reports/`, and direct
   inspection of `assay/src/assay/` when a fix is claimed. "Implemented on
   branch X" is DONE only when that work is verifiably an ancestor of `main`
   (`git merge-base --is-ancestor <commit> main`).
3. Verdict vocabulary (uniform across every entry): DONE, PARTIAL, OPEN,
   DEFERRED, BLOCKED, SUPERSEDED, WITHDRAWN, UNCLEAR.

## Verdict table

Legend: verdict as of 2026-09-23. "cite" = commit hash and/or CHANGES.md
line and/or decisions.md A-nnn row and/or file:line.

<!-- TABLE-IN-PROGRESS: filled in incrementally as each range is audited -->

| id | verdict | date/version | evidence |
|---|---|---|---|

## WIP-branch findings

<!-- filled in once the branch-investigation passes return -->

## ID collisions

<!-- filled in once the P35/B099 investigation returns -->

## UNCLEAR items

<!-- entries where evidence was genuinely insufficient -->

## Entries whose prose is factually wrong vs. the code

<!-- entries whose own claimed status/prose contradicts what the audit found -->
