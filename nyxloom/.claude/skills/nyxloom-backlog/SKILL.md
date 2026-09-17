---
name: nyxloom-backlog
description: File a bug, feature request, or follow-up finding into a target tool's managed nyxloom backlog (nyxloom-trove/backlog/ per-entry files) — search-before-file, note-vs-new judgment, CLI mechanics. Use when asked to "file an issue", "add to the backlog", "record this finding upstream", or when a work session in one project uncovers a defect in a TOOL owned by another repo (ciu, assay, cmru, nyxloom, run-gate).
---

> **Tool versions as of last verified update (2026-09-03):** nyxloom
> `0.3.1.dev1263+gf3b89f46` — `nyxloom backlog {new,promote,note,set-status,
> list,show,index}` verified present and matching this text's verb set.
> **Not yet true of every vbpub tool**: this skill's own §"the estate rule"
> notes some targets (ciu, run-gate) still use a legacy single-file
> tracker (`KNOWN_ISSUES_TODO_BACKLOG.md`) rather than the managed
> per-entry `nyxloom-trove/backlog/` — check the target's own `nyxloom.toml`
> for `[backlog_entries]` before assuming the CLI path applies.

> **Estate-level skill.** Applies to any dstdns/vbpub work session, in either
> direction (a defect found in a tool while working in a consumer repo, or
> vice versa).

# Filing backlog entries (nyxloom managed per-entry backlog)

## The estate rule this skill implements

Findings about a TOOL are filed in the tool's OWN repo, never as a local
workaround in the repo where the tool misbehaved (root `AGENTS.md`,
cross-repo layer). Each vbpub tool's backlog lives at
`nyxloom-trove/backlog/` once that project adopted `[backlog_entries]`
(check its `nyxloom.toml`); until migrated, it may still be a legacy
single big file (`KNOWN_ISSUES_TODO_BACKLOG.md` or `4-backlog.md`) — file
there per that file's own conventions.

This also covers **cross-repo reusability findings**: a script/mechanism
written locally in a consumer repo (e.g. a shell script doing something a
CLI tool could do generically) is itself a backlog candidate on the tool it
resembles or a new-tool proposal — see "Gather before writing" point 2 below;
don't let a locally-useful script silently stay local forever if the estate
would benefit from it becoming a real capability.

## Workflow

1. **Identify the target repo.** The tool that owns the misbehavior, not the
   repo you are working in. vbpub tools: `ciu`, `cmru`, `assay`, `nyxloom`,
   `run-gate`, `pwmcp` — all under `/workspaces/vbpub/`.

2. **Check CHANGES.md / recent releases first.** The defect may already be
   fixed on main or in a newer release than the one that misbehaved. Verify
   the version you observed against the tool's latest.

3. **Search existing entries before filing new.** `ls`/grep the target's
   `backlog/` dir (or read the legacy file's index table). Decision:
   - Same underlying defect → `nyxloom backlog note <ID> "<evidence>"`.
     A second reproduction is PRIORITY EVIDENCE — say so in the note.
   - Genuinely new → `nyxloom backlog new`.

4. **Gather before writing** (the five-point discipline the entry template
   scaffolds): observed mechanism + a live or source-grounded reproduction;
   why the TARGET tool owns it (not the consumer); proposed contract /
   refusal states; behavioral oracles incl. a controlled wrong
   implementation; the SPEC section that owns the behavior. Never file an
   entry whose mechanism you have not confirmed in source or live.

5. **File it:**

   ```bash
   cd /workspaces/vbpub/<tool>
   nyxloom backlog new "<mechanism in one line>" \
       --type bugfix --severity medium \
       --provenance "<originating repo> <package/report id>" \
       --body-from /tmp/opencode/entry-body.md
   ```

   Write the body (the five sections) to a temp file first — better prose
   than shell-escaped inline text. The command prints the created path;
   `git add` the new file AND the regenerated `INDEX.md` when you commit.

6. **Cross-repo annotations.** Reference commits in the other repo as
   `dstdns@<hash>` / `vbpub@<hash>` (estate convention). Record the
   originating report path (e.g.
   `dstdns/nyxloom-trove/reports/dstdns-P111-REPORT.md §9 F2`) in the
   `provenance` field — the consumer repo keeps only the pointer.

## Status transitions (only via the CLI)

- `nyxloom backlog set-status <ID> fixed|withdrawn|obsolete --reason "..."`
  — reason is REQUIRED; the verb stamps the date.
- `merged` is written only by the merge flow. Never hand-set it.
- Reopen: `set-status <ID> open --reason "recurred: ..."` (clears closed_*).

## Hard rules

- NEVER hand-edit `backlog/INDEX.md` — regenerate (`nyxloom backlog index`);
  lint fails on staleness.
- NEVER put follow-up evidence in a new entry when the defect exists —
  `note` the existing entry (misfiled evidence is the #1 measured failure
  of the old big-file trackers).
- A WITHDRAWN entry must never remain described as a shipped capability
  anywhere.
- Do not allocate ids by hand — `backlog new` allocates; two agents
  allocating by hand is how the estate got the CIU-28→39 renumber.
