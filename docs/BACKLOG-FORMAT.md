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
`INDEX.md` replaces it. Every `##` heading is an entry. Migration drops the old
evidence-scrub comments; git keeps them.

## Entry

```
## CTL-55 — a crashed wrapper leaves a stale `.live` file

**State:** blocked · **Severity:** medium · **Owner:** platform · **Links:** blocked-by CTL-40, scripts/controller/lib.sh

**Problem.** … **Wanted.** …
```

- **Header:** `## <ID> — <title>`, one line, U+2014 with one space each side.
  Lines inside code fences are not headers. At migration, a `##` that is not an
  entry becomes `###` in its entry, and a `###` entry becomes `##`.
- **Metadata:** the first non-blank line after the header: four fields in this
  order, separated by ` · `. Tools read only header and metadata.
- **State:**
  - `open`: can be worked on, and nobody is on it.
  - `in-progress`: someone is on it. Links names the branch or run.
  - `blocked`: waiting. Links has `blocked-by <ID|operator|word>`.
  - `parked`: deferred on purpose. The body says what would revive it.
- **Severity:** three words, because every existing scale maps onto them.
  - `high`: wrong results, data loss, a false PASS, or a blocked release.
  - `medium`: a real defect with a workaround, or a costly gap.
  - `low`: hygiene, cosmetic or a wish.
- **Owner:** one token for who acts next (topic or session name, `operator`,
  or `-`).
- **Links:** comma-separated IDs, `repo@sha`, paths, branches or SPEC IDs, or
  `-` for none. `blocked-by <ID|operator|word>` and `reopens <ID>` are the only
  relation prefixes. `check` does not resolve their targets.
- **Body:** free Markdown (`###` and lower): current problem and wanted
  outcome. No dated update log, because git keeps it. Long evidence goes in a
  linked report.

Default severity mapping for migration (the migrator may re-judge an item):
Must-fix, P1, Major, High, Critical → `high`; Should-fix, P2, Medium →
`medium`; Nice-to-have, P3, Minor, Low, Enhancement, none → `low`. Severity: any
other word → `low`.

## IDs

- The prefix comes from the marker and is unique in vbpub.
- An ID is `<prefix><digits>`, optionally followed by one lowercase letter. Two
  IDs are equal when prefix, integer value and letter are equal, so `CTL-4` =
  `CTL-04` but `FD-007` ≠ `FD-007b`.
- A file keeps its existing padding. New files use none.
- Existing suffixes such as `FD-007b` stay valid, but no new ones are created.
- Archive lines may keep a legacy prefix. An open entry with one is renumbered
  at migration, its title ending `(was FEAT-01)`.
- The next free ID is 1 + the largest N in the backlog and all its archive
  files. An ID is never reused.

## Closing and reopening

To close an item, remove its entry and append one line to `archive/<YYYY-QN>.md`
(the quarter of the UTC close date). Do this in the fix commit or the next one.

```
CTL-12 | remote-gate helper refreshes through a shared `.new` temp name | 3f9a1c2d7 | 2026-11-16
```

- **Fields:** ID, title, closing commit and `YYYY-MM-DD`, separated by ` | `. A
  title therefore has no `|`.
- **Closing commit:** the fix commit on main, 7 to 40 hex characters, with a
  `dstdns@` prefix for a dstdns commit. When nothing was fixed, use `withdrawn`,
  `by-design`, `duplicate` or `superseded`. Use `-` only at migration, when the
  entry names no commit; don't dig through history for one.
- The archive is append-only; narratives stay in git history.
- An archive file is a `# <project> archive <YYYY-QN>` line followed by archive
  lines; blank lines are allowed, nothing else is. tls-edge's file is rewritten
  at its migration, and its KI-115 line is dropped because cmru tracks it.
- **Reopen:** a regression gets a new ID with `reopens CTL-12`; the archive line
  stays. A mistaken close is undone in one commit.
- A limitation kept by design is not an item. It moves to the limitations
  section of the SPEC or README and is archived as `by-design`.

## INDEX.md and check

`agctl backlog index [FILE…]` writes `INDEX.md` beside each backlog. `index`
takes the same default files as `check`. It writes `# <project> backlog index`
and the line `Generated by agctl backlog index; do not edit.` Then come
`## By state` (`### open`, `in-progress`, `blocked`, `parked`), `## By owner`
(`### <owner>` in byte order) and `## Blocked`. Empty sections are omitted.
Rows are a table `| ID | Title | Severity |`; Blocked adds `Blocker`, all
`blocked-by` targets joined by `, `. Rows sort high, medium, low, then by ID.
The ID cell links `KNOWN_ISSUES_TODO_BACKLOG.md#<GitHub slug of the header>`.
No timestamp, so unchanged input gives identical bytes.

`agctl backlog check [FILE…]` (default: every tracked file with the marker)
fails only on these, printing one `path:line: reason` each and exiting 1:

- an unparsable header or metadata line (unknown word, missing or misordered
  field);
- an entry ID that does not match the prefix;
- a marker prefix that another marker file also uses;
- a duplicate ID in the backlog or across its archive files;
- an ID that is in both the backlog and the archive;
- a malformed archive line, or one in the wrong quarter;
- `blocked` without `blocked-by`;
- a stale `INDEX.md` (differs from regenerated output).

ctl-suite runs `check` for now. Later the estate lint lane (RG-296) runs it.

`agctl backlog next` (the next free ID) is planned, not specified here.

## Migration

Each file migrates on first touch, in one commit for that file. cmru, ciu and
run-gate-project migrate on their own branches. The pilots are
`scripts/controller` and `mdt-cockpit` (P-plat-16).

The commit changes only the shape: marker, preamble, no status table, headers
and metadata, closed entries archived, `INDEX.md`, and the tests that read the
backlog text (e.g. `mdt-cockpit/host-setup/tests/test_io_governance.py` looks
for MDT-016). Content edits come in later commits. A test that pins an archived
entry now asserts its archive line or is dropped. A test that parses the status
table is replaced by `check`. Anchor links to entries are rewritten in the same
commit.

State mapping: OPEN, PLANNED → `open`; PARTLY FIXED, PARTIAL, PARTLY DONE,
PARTIALLY FIXED → `open`, the body keeping only what remains; IMPLEMENTED or
PENDING on an unmerged branch → `in-progress`; DEFERRED → `parked`; FIXED, DONE,
CLOSED, RESOLVED, SHIPPED, merged IMPLEMENTED, WITHDRAWN, SUPERSEDED, BY DESIGN
→ archive, dated as the entry states, else the migration date; any other word →
`open`; the migrator judges.
