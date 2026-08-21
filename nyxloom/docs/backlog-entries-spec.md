# nyxloom backlog entries — spec (per-entry managed issue records)

> Status: spec · 2026-08-21 · the managed `nyxloom-trove/backlog/` directory —
> one file per issue/feature entry, YAML frontmatter + markdown body, lint-
> enforced index, promotion from the idea inbox. This is the CONTRACT the
> implementation wave implements. Companion to `spine-documents-spec.md`
> (which it amends: the spine inbox file is renamed, see §Compatibility).

## Problem (evidence, not taste)

Three vbpub tools track issues in single large hand-maintained markdown files
(`ciu/KNOWN_ISSUES_TODO_BACKLOG.md` 641 lines, `cmru/KNOWN_ISSUES_TODO_BACKLOG.md`
681, `assay/nyxloom-trove/4-backlog.md` 1146). Measured failure modes, all
present in ciu's file as of 2026-08-21:

1. **Misfiled content** — two second-reproduction paragraphs describing
   CIU-41/CIU-42 landed inside WITHDRAWN CIU-23's section; their
   "priority bump warranted" note never reached the right rows.
2. **Table-vs-body drift** — CIU-43's second reproduction exists only in the
   status-table cell; the entry body was never updated. Updates land wherever
   the filer happened to look.
3. **Structural rot** — an empty `### CIU-39 detail` heading (High-severity
   OPEN issue, zero body); five issues' details nested under a withdrawn
   issue's heading; unsorted status table; a full design embedded in one table
   cell; status maintained in BOTH table and prose.
4. **Fragile consumer pointers** — consumer projects "keep only pointers"
   into the big file; every anchor is a long heading fragment that breaks on
   restructure.

Root cause: one file forces three jobs (tracker index, issue record, change
history) into one surface, so every edit trades off which job gets done
sloppily. Per-entry files give each issue its own surface; a GENERATED index
restores the at-a-glance view without hand-maintenance.

## Format decision (resolved)

**Markdown + schema-validated YAML frontmatter, one file per entry** — the
house pattern (handoffs, spine docs). The machine trusts only the frontmatter
(JSON-schema validated, `nyxloom lint`-checked); the body is human narrative
the machine never parses for correctness. Reuses `frontmatter.py`, the schema
directory, and the L-rule lint infrastructure. No new format.

## The two tiers

| tier | home | holds | format |
|---|---|---|---|
| **idea inbox** | `4-backlog-inbox.md` (spine) or plain `backlog.md` | quick, un-carved ideas | unchanged: spine YAML `items:` or P28 bullets |
| **managed entries** | `nyxloom-trove/backlog/` | filed issues, bugs, feature asks with contracts/oracles/reproductions | one `.md` per entry |

The inbox stays deliberately lightweight — capture cost near zero. An idea
that grows a contract, oracles, or a filed provenance gets PROMOTED
(`nyxloom backlog promote`) into a managed entry; the promotion removes it
from the inbox. Nothing else about the inbox changes: P28 machinery
(`backlog_items.py`, BLG1, auto-tick) keeps serving it untouched.

## Location and config

Entries live under the trove, declared in `nyxloom.toml`:

```toml
[backlog_entries]
dir       = "nyxloom-trove/backlog"   # default shown; trove-relative
id_prefix = "CIU"                     # required; the project's issue sequence
```

- **Omitting the section means the project does not use managed entries** —
  every new lint rule is silent, no scaffold changes. Adoption is opt-in,
  same as the spine.
- `id_prefix` is per-project so estate sequences survive: ciu `CIU`,
  cmru `KI`, assay `B`, nyxloom `NL` (defaults chosen at each migration, not
  hardcoded here).

## Entry file contract

### Naming

Filename: `<ID>-<slug>.md` — e.g. `CIU-41-env-generate-ambient-network.md`.

- `<ID>` = `<PREFIX>-<NN>` (recommended form; `CIU-41`, `KI-12`). Legacy
  dash-less padded shapes (`B001`) remain VALID so a migrating project keeps
  its existing ids and pointer stability; the id grammar is therefore
  `^[A-Z][A-Z0-9]*-?[0-9]+$`. New prefixes use the dashed form.
- `<slug>`: lowercase kebab, `[a-z0-9-]+`, 1–63 chars, REQUIRED (greppability
  without opening the file).
- **Filename stem minus the slug MUST equal the frontmatter `id`** (lint
  BLG2, the L1 precedent adapted: stem = `<id>-<slug>`).

### Frontmatter (schema: `schemas/backlog-entry.schema.json`)

```yaml
---
kind: backlog-entry          # const
schema_version: 1            # const
id: CIU-41                   # required, unique, grammar above
title: "env generate silently inherits ambient DOCKER_NETWORK_INTERNAL"
status: open                 # required; vocabulary below
type: bugfix                 # optional: feature | bugfix (spine-aligned)
severity: medium             # optional: low | medium | high
priority: 2                  # optional int; lower = sooner
component: workspace-env     # optional, free string (carve grouping hint)
context_estimate: small      # optional: small | medium | large (carver input)
folds_into: F019             # optional; product-definition feature ref
filed_by: "dstdns P111 Mode-B live pass"   # optional, free
filed_date: "2026-08-20"     # optional, "YYYY-MM-DD" STRING (quoted — an
                             # unquoted YAML date parses as datetime.date
                             # and fails the string-typed schema)
provenance: "dstdns/nyxloom-trove/reports/dstdns-P111-REPORT.md §9 F2"
spec_owner: "S2"             # optional; which SPEC section owns the behavior
decisions: ["D-154"]         # optional; D-NNN refs, decisions.md namespace
carved_handoff: nyxloom-P29-intake-agent   # optional; set when carved
merge_commit: abc1234        # optional; stamped by the merge auto-tick ONLY
promoted_from: B7            # optional; inbox id this entry was promoted from
closed_date: "2026-08-25"    # optional; stamped by set-status on terminal states
closed_reason: "..."         # optional; REQUIRED for fixed|withdrawn|obsolete
---
```

Required: `kind`, `schema_version`, `id`, `title`, `status`. Everything else
optional; `additionalProperties: false`.

### Body

Human narrative, machine never parses it. `nyxloom backlog new` scaffolds the
section template distilled from ciu's filing discipline (generalized to any
project; sections may be deleted when they don't apply):

```markdown
## Observed mechanism and reproduction

## Why <project> owns it

## Proposed contract

## Oracles

## SPEC ownership

## Updates
<!-- dated one-paragraph appends land here: `nyxloom backlog note` -->
```

The five-point shape is RECOMMENDED, not enforced (BLG rules check structure,
never prose). `## Updates` is where follow-up evidence goes — second
reproductions, priority bumps — as dated paragraphs appended by
`nyxloom backlog note <ID> "..."`. This is the mechanical fix for failure
mode #2: follow-ups have exactly one home, and it is not a table cell.

## Status model

Vocabulary (extends P28's `open|carved|merged` — the inbox keeps that trio
unchanged; this vocabulary is for entries):

| status | meaning | set by |
|---|---|---|
| `open` | filed, not started | `new` / `promote` |
| `carved` | a handoff exists for it | carver flow sets `carved_handoff` |
| `merged` | the carving landed | **auto-tick only** (merge hook) |
| `fixed` | resolved without/aside from a carve | `set-status --reason` (req.) |
| `withdrawn` | premise disproved; behavior removed/never adopted | `set-status --reason` (req.) |
| `obsolete` | superseded; nothing left to prove | `set-status --reason` (req.) |

Transitions: `open ↔ carved` free; anything → `fixed|withdrawn|obsolete`
requires `--reason` and stamps `closed_date` + `closed_reason`; terminal
states reopen only via explicit `set-status open --reason "reopened: …"`
(which clears `closed_*`). `merged` is never hand-set — it exists so the
merge hook is the only writer, mirroring `tick_merged`'s discipline.

WITHDRAWN semantics inherit the estate rule already written into ciu's
tracker: a withdrawn entry must not remain described anywhere as a shipped
capability.

## INDEX.md — generated, lint-enforced

`backlog/INDEX.md` is GENERATED by `nyxloom backlog index` and starts with a
literal `<!-- GENERATED ... do not edit -->` banner:

```
| ID | Title | Status | Type | Priority | Provenance |
```

Sorted: `open` first, then `carved`, then terminal statuses; id-ascending
(numeric) within each group. One row per entry, drawn purely from
frontmatter.

Enforcement (the estate's "make it a test, not an intention"): lint rule
**BLG3** regenerates the index to a temp buffer and byte-compares; a stale
index is a lint ERROR. Drift between the tracker view and the entries is
therefore uncommittable rather than discouraged. Next-id allocation also
reads frontmatter ids (max + 1), so allocation can never collide with a
renamed/legacy sequence.

## CLI verbs (`nyxloom backlog <verb>`)

New subcommand group, wired like the existing `finding` group:

| verb | effect |
|---|---|
| `new "<title>" [--type --severity --priority --component --provenance --filed-by --spec-owner --body-from <file>]` | allocates next id, scaffolds the entry (body template above; `--body-from` slurps prepared prose), writes file, regenerates INDEX, runs the BLG checks on the result, prints the path |
| `promote <inbox-id>` | inbox item → managed entry: creates the entry carrying over `title/type/component/context_estimate/folds_into`, stamps `promoted_from`, REMOVES the item from the inbox, regenerates INDEX, lints |
| `note <ID> "text"` | appends a dated paragraph under `## Updates` (the only sanctioned way to file follow-up evidence) |
| `set-status <ID> <status> [--reason "..."]` | typed transition per the table above; refuses `merged`; stamps/clears `closed_*` |
| `list [--status S]` | prints INDEX (optionally filtered) |
| `show <ID>` | prints the entry file |
| `index` | regenerates INDEX.md |

**Promotion edits the inbox SURGICALLY** — line-range deletion of exactly the
item's block (bullet+continuations+header for the plain form; the item's
YAML block-sequence lines for the spine form), never a YAML re-dump. PyYAML
round-trips destroy comments and formatting; the byte-preserving discipline
is `tick_merged`'s, reused.

**Merge auto-tick extends to entries**: where `cmd_merge` today calls
`tick_merged()` against the single-file inbox, it additionally scans
`[backlog_entries].dir` for an entry whose `carved_handoff == <task>` and
rewrites ONLY that entry's frontmatter tokens `status=merged`,
`merge_commit=<commit>` (surgical token substitution, same discipline). No
match → no write, as today.

## Lint rules

Active only when `[backlog_entries]` is declared (absent section ⇒ silent):

- **BLG2 — entry validity** (per file): frontmatter against
  `backlog-entry.schema.json`; stem-minus-slug equals `id`; id grammar;
  uniqueness across the dir; `closed_reason` present iff status is terminal
  (except `merged`, which carries `merge_commit` instead).
- **BLG3 — index freshness**: regenerated INDEX byte-equals the committed
  INDEX.

Rule numbering continues the BLG namespace (BLG1 = inbox headers, unchanged).

## Claude skill (`~/.claude/skills/backlog`)

A thin judgment wrapper over the mechanics; the CLI does the writing, the
skill encodes the estate's cross-repo filing rule (root `AGENTS.md`:
findings about a TOOL are filed in the tool's own repo, checking its
CHANGES.md first). Workflow the skill instructs:

1. Identify the target tool repo and locate its `[backlog_entries]` dir
   (read its `nyxloom.toml`; fall to the legacy file only if the project has
   not migrated).
2. Search existing entries before filing: a follow-up reproduction or
   priority bump is `backlog note` on the EXISTING entry, never a new one
   (failure mode #1).
3. New entry: gather mechanism + repro + ownership argument first, then one
   `nyxloom backlog new` call; never hand-edit INDEX.
4. Cross-repo annotations: reference the other repo as `dstdns@<hash>` /
   `vbpub@<hash>` per estate convention.

Authored as part of the implementation wave (not now), so its instructions
cite shipped verb names.

## Compatibility and deprecations

- **Spine inbox renamed**: `4-backlog.md` → **`4-backlog-inbox.md`**. The
  path is config-declared (`[project] backlog`), so lint needs no change —
  the rename lands in: this spec, `spine-documents-spec.md`, STANDARD.md,
  the `exec-nyxloom init` scaffold/template, and each adopting project's
  `nyxloom.toml` value (a `git mv` + one-line config edit per project).
  Rationale: "backlog" now names the managed entries; the inbox name should
  say inbox.
- **Plain `backlog.md` and all P28 machinery**: unchanged, still supported
  for projects that never adopted the spine. `backlog_items.py`'s frozen
  interface contract is untouched; entries are a parallel module
  (`backlog_entries.py`), not a modification of it.
- **No `[backlog_entries]` section**: byte-for-byte today's lint/scaffold
  behavior.

## Documentation obligations (estate mandate — in the wave, not after)

Per root AGENTS.md, this capability ships only with all three synced, made
testable per the docs-sync doctrine:

- **README**: feature bullet (managed per-entry backlog + promotion),
  linking DESIGN-GUIDE for rationale.
- **DESIGN-GUIDE** (`docs/`): this spec IS the design authority; the guide
  links it and records the rejected alternative (keeping single big files)
  with the ciu evidence.
- **CONSUMERS.md** (`docs/`): a paste-able adoption recipe — add the
  `[backlog_entries]` table, run `backlog new`, wire the gate — plus the
  migration recipe outline for legacy big-file trackers.
- STANDARD.md gains the `backlog/` trove line + a short section pointing
  here; `spine-documents-spec.md` is amended for the inbox rename.

## Tests (acceptance floor)

- Unit: entry parse/validate (schema violations, stem≠id, duplicate ids,
  legacy id shapes); next-id allocation across mixed legacy/dashed shapes;
  promote from BOTH inbox forms with byte-preservation assertions on the
  surviving inbox text (comments survive); `note` appends; `set-status`
  transitions incl. refusal paths (missing `--reason`, `merged` target);
  index determinism (two runs byte-equal); entry auto-tick writes only the
  two tokens.
- CLI: every verb happy path + refusals; `--body-from` missing file.
- Lint: BLG2/BLG3 fire and silence conditions; absent-section silence.
- Docs-sync: every TOML/YAML example in README/CONSUMERS/this spec parses
  with the SHIPPED loader and declares current schema versions; cross-doc
  anchors resolve.

## Implementation phasing (after this spec is approved)

- **Package A** — `backlog_entries.py` module + `backlog-entry.schema.json`:
  parse/validate/allocate/new-file scaffolding/index generation/promote
  primitives/note/set-status/entry auto-tick. Tests.
- **Package B** — CLI subcommand group + merge-hook extension. Tests.
- **Package C** — lint BLG2/BLG3 + `exec-nyxloom init` scaffold (commented
  `[backlog_entries]` template, renamed inbox in the spine template). Tests.
- **Package D** — docs (README/DESIGN-GUIDE/CONSUMERS/STANDARD/spine-spec/
  this spec's final review) + the `backlog` skill + CHANGES.md + version
  bump. Release via cmru.
- **Migration wave (separate, post-release)** — convert ciu/cmru/assay big
  files to entries (including FIXING the misfiled CIU-23 content and the
  CIU-43 table-only repro during conversion), repoint root AGENTS.md's
  cross-repo filing rule, retire the legacy files to archive. Each project
  picks its `id_prefix` and keeps its historical numbers.
