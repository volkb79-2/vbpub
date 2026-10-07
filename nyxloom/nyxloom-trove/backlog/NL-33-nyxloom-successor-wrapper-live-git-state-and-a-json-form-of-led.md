---
kind: backlog-entry
schema_version: 1
id: NL-33
title: "nyxloom successor wrapper (live git state) and a JSON form of ledger/effects/stop state"
status: open
type: "feature"
severity: "medium"
component: "session_extract"
provenance: "nyxloom SUCCESSOR package 2026-10 (E-020)"
filed_by: "Claude Sonnet (SUCCESSOR implementer)"
filed_date: "2026-10-06"
---

**Observed mechanism.** After the SUCCESSOR package the controller still gathers live state by hand (skill step 3: `git status/log/worktree list`, `docker ps` by exact name, `ps`), and the ledger / stop state exist only as text inside the extract (no `--json` equivalent; the JSON extract does not carry them).

**Proposed contract.** (a) A `nyxloom successor <id> --order @FILE` wrapper (design phase 3) that runs the extract with the successor defaults, appends a read-only live-state section from the transcript `cwd`/branch, and writes the file. (b) A schema'd `--json` form of the whole-session ledger, effects and stop state (`nyxloom.successor-brief/1` as drafted in the design doc section 4).

**Why nyxloom owns it.** It is the extract's own output surface; scripts should not scrape markdown headings.

**Oracles.** Text/JSON parity test over the successor fixtures; the wrapper test uses a temp git repo and asserts the state section lists a dirty file.

**Spec owner.** `src/nyxloom/session_extract/README.md`, `docs/CLI-REFERENCE.md`.
