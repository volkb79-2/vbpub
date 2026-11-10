---
kind: backlog-entry
schema_version: 1
id: NL-59
title: "add a `readback` preset controlled primarily by `--budget-tokens N`, with priority filling and a footer naming what was dropped"
status: open
type: "feature"
severity: "high"
component: "session-extract"
provenance: "controller session record 2026-11-10; backlog filing for the operator"
filed_date: "2026-11-10"
---

## Problem

Existing extraction presets target live watch, successor priming, a full audit, or the effects ledger. None produces the bounded handoff a controller actually reads back into another model: preserve operator instructions and answers first, retain the most useful explanatory prose next, then include optional agent communication, intent lines, and external run results only as budget permits. The present hard stop is a word count (`ExtractConfig.max_words`) and its walk is organized around recency, so it cannot enforce a token-shaped budget by content priority or explain exactly which useful categories were omitted.

## Evidence

The controller session record 2026-11-10 measured a 5.5-hour controller session at about 200 KB / approximately 50k tokens with everything retained. That is too large for a predictable readback budget, while the omitted task notifications alone accounted for 31 KB / 7.7k tokens and should remain optional rather than dominate the selection. Current preset documentation lists only `watch`, `successor`, `review`, and `ledger`.

## Proposed minimal solution

Add `readback` as a fifth fixed-span preset and make `--budget-tokens N` its primary control. Keep `--max-words` available for other presets and all modes, but do not run both budget algorithms implicitly in `readback`.

Fill the budget in this order:

1. operator turns and answered AskUserQuestion pairs, as complete records, newest first within the category;
2. assistant prose;
3. agent communication, respecting NL-58's `--agent-comms` mode;
4. explicit paired `Intent:` lines;
5. external run-log results.

Never truncate a record already accepted. When a complete record does not fit, skip it and continue with the next item in the same category, then the next category. Compute one documented deterministic token estimate over the exact rendered envelope (the simplest acceptable implementation is a conservative UTF-8 byte-based estimator, with its formula printed in the footer); call the number "estimated" rather than claiming tokenizer-exact accounting. Record the estimator and resolved limit in the output so a consumer can reproduce the selection.

The footer must list, by category: kept count, dropped count, and dropped bytes/tokens; explicitly say when an operator turn or interview answer did not fit; identify whether agent communication or external run logs were disabled or budget-dropped; and include the resolved source span. A `--budget-tokens` value is required for `readback` (or the preset applies one documented explicit default and prints it); zero and malformed values refuse. Explicit selection/profile options continue to win over preset expansion.

## Where

- `nyxloom/src/nyxloom/session_extract/presets.py`: preset table, help, examples, and explicit-override rules.
- `nyxloom/src/nyxloom/session_extract/config.py`: token budget and estimator identifier.
- `nyxloom/src/nyxloom/session_extract/select.py`: priority fill replacing the current single backward walk for this preset.
- `nyxloom/src/nyxloom/session_extract/render.py`: footer and dropped-category disclosure.
- `nyxloom/src/nyxloom/cli.py`: option grammar, preset resolution, and help.
- `nyxloom/tests/test_session_extract_presets.py`, `test_cli_extract.py`, and render/selection tests.

## Acceptance test idea

Build a synthetic session with large operator turns, interviews, prose, agent communications, intent lines, and run results. Select with fixed budgets and assert category priority, no partial kept record, deterministic output for repeated runs, and exact footer counts. A budget just below the newest operator turn must disclose that dropped operator material rather than silently filling with lower-priority prose. Supplying an explicit contradictory option must override the preset. Re-applying recency-only selection makes the priority ordering case fail.

## Priority

High for controller workflows: the measured whole-session size is already about 50k tokens, and the preset makes readback size intentional, reproducible, and auditable instead of incidental.
