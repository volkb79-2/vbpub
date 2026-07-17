---
schema_version: 1
id: nyxloom-P28-backlog-schema-autotick
project: nyxloom
title: "Backlog-item light schema + daemon typed auto-tick on merge"
tier: sonnet5-high
input_revision: "e329de2"
depends_on: [nyxloom-P24-config-schema-lint]
session: fresh
source: {kind: backlog, ref: nyxloom-trove/4-backlog.md}
scope:
  touch:
    - "src/nyxloom/schemas/backlog-item.schema.json"
    - "src/nyxloom/backlog_items.py"
    - "src/nyxloom/cli.py"
    - "src/nyxloom/lint.py"
    - "tests/test_backlog_items.py"
  forbid:
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/config.py"
oracles:
  - id: O1
    observable: "A backlog item carrying a structured header block (id `B<N>`, status one of {open,carved,merged}, optional priority int, optional links to a carved handoff id / D-decision ids) validates via `backlog_items.parse(path)` + the new `src/nyxloom/schemas/backlog-item.schema.json`; a malformed item (missing id, status outside the enum, non-int priority) yields a blocking lint finding (rule id in a new `BLG*` namespace) from `lint.lint_project(cfg)`. A valid `nyxloom-trove/4-backlog.md` yields zero backlog findings."
    negative: "a backlog item with a bad status/priority lints clean and is only caught (or silently mis-parsed) at merge time"
    gate: tester-unified
  - id: O2
    observable: "`nyxloom merge <project> <task>` (cli.py cmd_merge), when the merged task's id is referenced by a backlog item's `carved_handoff` link, auto-updates ONLY that item's typed fields — status -> `merged`, and a `merge_commit` link set to the recorded SHA — writing the file back in place. A test records a merge for a task linked from a backlog item and asserts the item's status/commit fields changed."
    negative: "the backlog item stays `carved`/`open` after its handoff merged — the 'Status: line lies' problem this fixes"
    gate: tester-unified
  - id: O3
    observable: "The auto-tick is TYPED-ONLY: it edits solely the enumerated header fields (status, merge_commit) of the matched item and leaves every prose line (the `**B<N> — ...**` description body) byte-identical. A test asserts the non-header lines are unchanged after the tick, and that no other item is touched."
    negative: "the tick rewrites or reflows the item's prose, or edits sibling items (free-prose daemon write — violates the typed-fields-only / injection-boundary doctrine)"
    gate: tester-unified
  - id: O4
    observable: "Recording a merge for a task that NO backlog item links is a clean no-op: `nyxloom merge` still transitions the task to MERGED (existing behavior preserved) and the backlog file is left byte-identical. A test asserts zero backlog writes in that case."
    negative: "an unlinked merge throws, or spuriously rewrites/creates backlog content"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "a named contract cannot be met without touching a forbidden file (reconcile.py / daemon.py / config.py)"
  - "the structured-header format cannot coexist with the existing human-authored `**B<N> — ...**` prose without a lossy migration"
---

# P28 — Backlog-item light schema + daemon typed auto-tick on merge

Phase **α** of the feature-intake feature (backlog B9/B10). This is the
**foundation**: give backlog items a parseable structure and make the daemon
keep their status honest automatically on merge — a strictly TYPED write. It
unblocks P29 (the intake agent needs a structured item to persist a brief into)
and fixes the CLAUDE.md "`Status:` line is not self-updating" problem at its
root by making the merge the self-updating signal.

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P28-backlog-schema-autotick` from
`main`); commit all work on that branch. Do not touch the main checkout.

## Context to read first (read ONLY these, in order)

- `nyxloom-trove/4-backlog.md` — the doc being structured. Items are today free
  prose (`- **B<N> — title.** body...`). The new format adds a small, OPTIONAL
  machine-readable header per item (keep the prose body); design it so existing
  items remain valid (un-headered = status `open`, no links) — no lossy
  migration.
- `src/nyxloom/schemas/nyxloom-config.schema.json` + `src/nyxloom/lint.py`
  (both from P24, merged) — the schema-file + lint-rule PATTERN to mirror.
  P24 added config-schema validation with a `CFG*` rule namespace and a
  `lint.lint_config(cfg)` that folds into `lint.lint_project(cfg)`; add the
  analogous `backlog_items` validation + `BLG*` rules the same way.
- `src/nyxloom/decisions.py` — the EXISTING structured-doc precedent: it parses
  `decisions.md` D-NNN entries into typed `Decision` objects and finalizes them
  in place (`decisions.decide()`). Mirror its parse/serialize discipline
  (in-place edit of one entry's typed fields, prose untouched).
- `src/nyxloom/cli.py` `cmd_merge` (the function that records a merge:
  transitions MERGE_READY->MERGED + appends MERGE_RECORDED with the commit SHA).
  Your auto-tick hook goes HERE, after the merge is recorded — resolve the
  linked backlog item and tick it. This is the correct seam for merge_mode
  manual (a merge only happens through cmd_merge).

## Work

1. `src/nyxloom/schemas/backlog-item.schema.json`: a JSON schema for a backlog
   item header — `id` (`^B[0-9]+$`), `status` (enum open|carved|merged),
   `priority` (int, optional), `carved_handoff` (task-id string, optional),
   `decisions` (array of D-ids, optional), `merge_commit` (sha string, optional).
2. `src/nyxloom/backlog_items.py`: `parse(path) -> list[BacklogItem]` (tolerant
   of un-headered legacy items -> status open), `validate(items)` -> findings,
   and `tick_merged(path, task_id, commit) -> bool` (find the item whose
   `carved_handoff == task_id`; set status=merged + merge_commit; write back
   editing ONLY typed header fields; return False no-op if none match).
3. `src/nyxloom/lint.py`: add `BLG*` backlog findings (parse + schema-validate
   the backlog file) folded into `lint_project`, mirroring P24's `CFG*`.
4. `src/nyxloom/cli.py` `cmd_merge`: after the MERGE_RECORDED append, call
   `backlog_items.tick_merged(cfg.backlog, args.task, commit)` (best-effort;
   a no-op when unlinked). Do NOT change the merge state-transition itself.
5. `tests/test_backlog_items.py`: prove O1 (schema valid/invalid), O2 (tick on
   merge), O3 (prose untouched + siblings untouched), O4 (unlinked no-op).

## Scope / forbid

Touch ONLY the five files in `scope.touch`. The auto-tick must live in
`cmd_merge` (cli.py), NOT in `reconcile.py`/`daemon.py` (forbidden — keeps this
off P26's reconcile changes). Do not add `Policy` fields (config.py forbidden).

## BLOCKED rule

If a named contract cannot be met without a forbidden file, or the structured
header cannot coexist with existing prose without a lossy migration, STOP —
write `BLOCKED: <reason>` to the LOG, commit, and exit. Do NOT improvise.

## Gate

`tester-unified`:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
