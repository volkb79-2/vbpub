---
kind: backlog-entry
schema_version: 1
id: NL-71
title: "`--preset readback` has no goal anchor, recent-prose tail or end-of-document marker; the controller readback is delta-only"
status: fixed
type: "feature"
severity: "high"
component: "session-extract"
provenance: "operator decision 2026-11-15 that a controller session must survive AUTO-compaction without a human; readback = original goal prompt + recent prose + delta since the previous marker"
filed_by: "controller, pi-readback-auto package"
filed_date: "2026-11-15"
closed_date: "2026-11-15"
closed_reason: "Delivered on pi-readback-auto-20261115: session_extract/layout.py (goal anchor, tail, delta header, marker line, budget/truncation order), --goal-prompt/--goal-file/--tail-prose-tokens wired through the registered parser and the readback preset, an adapter slash_command tag, plus tests/test_session_extract_layout.py (fixture layout.jsonl, no-duplication across two consecutive readbacks, budget floor and truncation order) and scripts/controller/hooks/ctx-hooks.py with its offline suite."
---

## Problem

The shipped `readback` preset (NL-59, NL-65) emits the DELTA only: whatever
came after the previous read-back's `--since` marker. A session that survives
an automatic compaction needs up to three parts, in this order: a goal anchor
(the session's ORIGINAL goal prompt, so the resumed session knows what the work
is for), the most recent user/assistant prose from before the cut (about 10k
tokens; prose between the anchor and the tail may be lost on purpose), and the
delta since the previous read-back's marker. Without a tail a resumed session
cannot read its own last turns; without any anchor a spawned session that
inherits only state files and a delta cannot say why any of it matters.

The anchor is OPT-IN, not a preset default: the operator refinement of
2026-11-15 (root `AGENTS.md`) separates the two session kinds. A controller
session's anchor is its `plan.yaml` and its goal shifts, so its first prompt
would MISLEAD it; a spawned or targeted session's anchor is the goal it
started with. The preset cannot know the session kind, so it does not decide
(a default here would be a silent-invention default for the kind it guesses
wrong).

The reader also had no programmatic way to learn which marker to pass next:
the cursor exists only inside an HTML comment that `--since-file` can parse, so
a hook that must record "the previous read-back's marker" had to scrape the
comment or track it out of band.

## Wanted

1. A layout mode for the preset: `--goal-prompt` (the first genuine user
   prompt, harness framing and slash-command wrappers excluded) or
   `--goal-file PATH` to pin the anchor, and `--tail-prose-tokens N` for the
   recent-prose tail (complete records, newest first, user/assistant prose
   only). The tail is the preset's own value; the anchor is only ever explicit.
2. `--budget-tokens` covering the WHOLE document, with a fixed truncation
   order: tail prose shrinks first, then the delta, the anchor never.
3. A `[readback marker: <marker>]` line at the end naming the marker the next
   read-back must use, and a layout accounting footer (anchor source and cost,
   tail budget/kept/dropped, the delta's remaining content budget).
4. Tests: the three parts on a fixture transcript, no duplication across two
   consecutive read-backs, the budget bound and truncation order, the marker,
   anchor redaction, and the budget floor (an anchor larger than the budget is
   emitted in full and disclosed, never truncated).

## Close-when

The recent-prose tail belongs to `--preset readback` by default, the anchor is
available through the two documented flags, the three parts and the
no-duplication property are covered by fixtures, the output never exceeds the
budget above the anchor floor, the marker line ends the document, and the
README/DESIGN-GUIDE/CONSUMERS/CLI-REFERENCE documents describe the layout and
its options.
