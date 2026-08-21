# CONSUMERS.md — adopting nyxloom's managed backlog entries

Worked recipes for the per-entry backlog (`nyxloom-trove/backlog/`, one file
per issue). Design authority (WHY it exists, rejected alternatives):
[`backlog-entries-spec.md`](backlog-entries-spec.md).

## Adopt in a project (paste-able)

Add to `nyxloom-trove/nyxloom.toml`:

```toml
[backlog_entries]
dir       = "nyxloom-trove/backlog"   # optional; this is the default
id_prefix = "CIU"                     # your project's issue sequence
```

Then:

```bash
nyxloom lint                 # BLG2/BLG3 now active (silent before adoption)
nyxloom backlog new "clean leaves instance-scoped networks" \
    --type bugfix --severity medium --provenance "consumer P111 F4"
nyxloom backlog index        # regenerate backlog/INDEX.md (lint enforces freshness)
```

Commit `backlog/` including `INDEX.md` — it is generated, and `nyxloom lint`
fails when it is stale, so never hand-edit it.

Omitting the `[backlog_entries]` table entirely = feature unused; every
backlog-entry lint rule stays silent.

## File a follow-up on an existing entry

Second reproductions, priority bumps, new evidence — `note`, never a new
entry and never a hand-edit:

```bash
nyxloom backlog note CIU-43 "second repro on 6.3.0: volumes leak too"
```

This appends a dated paragraph under `## Updates` in
`CIU-43-clean-leaves-networks.md` and refreshes the index.

## Close an entry

```bash
nyxloom backlog set-status CIU-23 withdrawn --reason "consumer premise disproved"
nyxloom backlog set-status CIU-36 fixed --reason "shipped in ciu-P08"
```

Terminal statuses (`fixed|withdrawn|obsolete`) require `--reason`; the verb
stamps `closed_date`/`closed_reason` in the entry's frontmatter. `merged` is
never hand-set — the merge flow auto-ticks the entry linked by
`carved_handoff`.

## Promote an idea from the inbox

Quick idea in `4-backlog-inbox.md` (spine) or `backlog.md` (plain) outgrew
the inbox? Promote it — the entry is created, the item is removed from the
inbox, and the index is regenerated in one step:

```bash
nyxloom backlog promote B7
```

## Carve from an entry

Set `carved_handoff` in the entry's frontmatter (or let your carve flow do
it); when that handoff merges, the merge auto-tick sets `status=merged` +
`merge_commit`. A `carved` status with a `carved_handoff` link is all the
auto-tick needs.
