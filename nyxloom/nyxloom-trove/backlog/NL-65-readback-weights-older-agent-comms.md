---
kind: backlog-entry
schema_version: 1
id: NL-65
title: "`extract --preset readback` treats all prose alike: older agent communication should weigh less than recent decisions, and the preset should reach non-Claude controllers"
status: open
type: "feature"
severity: "medium"
component: "session-extract"
provenance: "operator direction, 2026-11-12: token budget is up to the controller; tweak the extract further, e.g. drop older agent communication, so not all prose is equal"
filed_by: "Claude Sonnet 5.5"
spec_owner: "src/nyxloom/session_extract/ readback preset; docs/CLI-REFERENCE.md"
filed_date: "2026-11-12"
---

## Problem

The shipped `readback` preset (NL-58, NL-59) keeps operator turns and interview
answers unconditionally and drops lower-priority material by class and age.
After a long run, agent prompts and results from many hours ago still compete
equally with fresh decisions inside one class: a 60k-token read-back spent
about 30 % of its budget on agent communication that had long been superseded,
and the controller needed the opposite weighting. The preset is also
Claude Code only (`claude_only`), while the controller rule in AGENTS.md applies
to any long-running controller, including Codex and OpenCode ones.

## Wanted

1. Age-weighted dropping inside the agent-communication and run-results
   classes (oldest first, with a configurable half-life or a `--comms-window`
   duration), reported in the existing footer (what was dropped, by class and
   time span).
2. An option to prefer outcomes over prompts for old agent communication (keep
   the result line, drop the brief) once the item is older than the window.
3. A documented fallback for controllers on Codex or OpenCode transcripts (either
   extend the preset to those adapters or state the equivalent `extract`
   options), so the AGENTS.md recipe holds for every controller.

## Close-when

`--preset readback` on a real long controller session with a fixed budget keeps
all operator turns and interview answers, drops older agent communication before
recent decisions (test with a fixture where old comms alone exceed the budget),
reports the weighting in its footer, and the Codex/OpenCode fallback is
documented and tested or the preset supports those adapters.
