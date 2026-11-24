# Backlog format

For every vbpub `<project>/KNOWN_ISSUES_TODO_BACKLOG.md`. No database: small
plain files plus a generated view. Out of scope (own readers): nyxloom trove
backlogs (`nyxloom-trove/backlog/`) and assay's `nyxloom-trove/4-backlog.md`.

## Files

| File | Holds |
|---|---|
| `KNOWN_ISSUES_TODO_BACKLOG.md` | Open items only. The name is kept because tests and pointers read it. |
| `archive/<YYYY-QN>.md` | One line per closed item, in the quarter of the close date. |
| `INDEX.md` | Generated views. Never hand-edited. |
| SPEC, README, docs | Current behaviour only (history: git, `CHANGES.md`; open work: backlog). |

One backlog per project, so there is one place to look; a second list such as
`mdt-cockpit/TODO.md` merges into it.

## Backlog file

Line 1 is the marker `<!-- backlog-format: 1; prefix: CTL- -->`; tools skip
files without it. Then `# <project> backlog` and at most 5 preamble lines (where
normative behaviour lives; links to `INDEX.md` and `archive/`). No status table:
`INDEX.md` replaces it. Every `##` heading is an entry.

## Entry

```
## CTL-55 — a crashed wrapper leaves a stale `.live` file

**State:** blocked · **Severity:** medium · **Owner:** platform · **Links:** blocked-by CTL-40, scripts/controller/lib.sh

**Problem.** … **Wanted.** …
```

- **Header:** `## <ID> — <title>` (em dash, one line).
- **Metadata:** the first non-blank line after the header: four fields in this
  order, separated by ` · `. Tools read only header and metadata.
- **State:**
  - `open`: can be worked on, and nobody is on it.
  - `in-progress`: someone is on it. Links names the branch or run.
  - `blocked`: waiting. Links has `blocked-by <ID|operator|event>`.
  - `parked`: deferred on purpose. The body says what would revive it.
- **Severity:** three words, because every existing scale maps onto them.
  - `high`: wrong results, data loss, a false PASS, or a blocked release.
  - `medium`: a real defect with a workaround, or a costly gap.
  - `low`: hygiene, cosmetic or a wish.
- **Owner:** one token for who acts next (topic or session name, `operator`,
  or `-`).
- **Links:** comma-separated IDs, `repo@sha`, paths, branches or SPEC IDs, or
  `-` for none. `blocked-by` is the only relation prefix.
- **Body:** free Markdown (`###` and lower): current problem and wanted
  outcome. No dated update log, because git keeps it. Long evidence goes in a
  linked report.

Default severity mapping for migration (the migrator may re-judge an item):

| Existing word | Severity |
|---|---|
| Must-fix, P1, Major, High, Critical | `high` |
| Should-fix, P2, Medium | `medium` |
| Nice-to-have, P3, Minor, Low, Enhancement, none | `low` |

## IDs

- An ID is `<prefix><N>`; the prefix comes from the marker, unique in vbpub.
- N compares as an integer, so `CTL-4` and `CTL-04` are the same ID.
- A file keeps its existing padding. New files use none.
- Existing suffixes such as `FD-007b` stay valid, but no new ones are created.
- The next free ID is 1 + the largest N in the backlog and all its archive
  files. An ID is never reused.
- Two branches picking the same ID fail `check` at the second merge, and that
  branch renumbers.

## Closing and reopening

To close an item, remove its entry and append one line to `archive/<YYYY-QN>.md`
(the quarter of the UTC close date). Do this in the fix commit or the next one.

```
CTL-12 | remote-gate helper refreshes through a shared `.new` temp name | 3f9a1c2d7 | 2026-11-16
```

- **Fields:** ID, title, closing commit and `YYYY-MM-DD`, separated by ` | `. A
  title therefore has no `|`.
- **Closing commit:** the fix commit on main, 9 or more hex characters, with a
  `dstdns@` prefix for a dstdns commit. When nothing was fixed, use `withdrawn`,
  `by-design`, `duplicate` or `superseded`. Use `-` only at migration, when the
  entry names no commit; don't dig through history for one.
- The archive is append-only; narratives stay in git history.
- **Reopen:** a regression gets a new ID and the link `reopens CTL-12`. The
  archive line stays, so every citation of that close stays true. Only a close
  made by mistake is undone in place: in one commit, delete the archive line and
  restore the entry under its own ID.
- A limitation kept by design is not an item. It moves to the limitations
  section of the SPEC or README and is archived as `by-design`.

## INDEX.md and check

`agctl backlog index [FILE…]` writes `INDEX.md` beside each backlog. It has three
views: by state, by owner, and blocked (ID, title, blocker). Rows sort by severity,
then ID, and link to entries. No timestamp, so unchanged input gives identical
bytes.

`agctl backlog check [FILE…]` (default: every tracked file with the marker)
fails on:

- an unparsable header or metadata line (unknown word, missing or misordered
  field);
- an ID that does not match the prefix;
- a duplicate ID in the backlog or across its archive files;
- an ID that is in both the backlog and the archive;
- a malformed archive line, or one in the wrong quarter;
- `blocked` without `blocked-by`;
- a stale `INDEX.md` (differs from regenerated output).

ctl-suite runs `check` for now. Later the estate lint lane (RG-296) runs it.

## Migration

Each file migrates on first touch, in one commit for that file. cmru, ciu and
run-gate-project migrate on their own branches. The pilots are
`scripts/controller` and `mdt-cockpit` (P-plat-16).

The commit changes only the shape: marker, preamble, no status table, headers
and metadata, closed entries archived, `INDEX.md`, and the tests that read the
backlog text (e.g. `mdt-cockpit/host-setup/tests/test_io_governance.py` looks
for MDT-016). Content edits come in later commits.

State mapping:

| Old state | New state |
|---|---|
| OPEN, PLANNED | `open` |
| PARTLY FIXED | `open`; the body keeps only what remains |
| IMPLEMENTED or PENDING on an unmerged branch | `in-progress` |
| DEFERRED | `parked` |
| FIXED, DONE, CLOSED, RESOLVED, SHIPPED, merged IMPLEMENTED, WITHDRAWN, SUPERSEDED, BY DESIGN | archive, dated as the entry states, else the migration date |
