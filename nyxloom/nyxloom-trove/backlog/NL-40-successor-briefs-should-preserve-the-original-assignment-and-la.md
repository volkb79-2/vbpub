---
kind: backlog-entry
schema_version: 1
id: NL-40
title: "Successor briefs should preserve the original assignment and later operator instructions"
status: open
type: "feature"
severity: "medium"
component: "session_extract"
context_estimate: "medium"
provenance: "CMRU Sonnet handoff audit, session cd05ec35-5b6d-4160-8f51-3ad7631b5200; operator feedback 2026-10-14"
filed_by: "Codex from operator request"
spec_owner: "session_extract/README.md; docs/CLI-REFERENCE.md"
filed_date: "2026-10-14"
---

## Observed mechanism and reproduction

During a handoff audit of the CMRU Sonnet session `cd05ec35-5b6d-4160-8f51-3ad7631b5200`, I needed to recover the exact originally assigned scope and distinguish it from later operator amendments. `nyxloom-harness extract <session> --preset successor --successor-brief` produced a useful single handoff with the original brief, selected history, a ledger, and stop state. It reduced an 8,586,438-byte transcript to a 50,994-byte, 6,976-word output.

The original brief was 7,218 characters, but `--brief-max-chars` defaults to 6,000. At the default, this original assignment would have been externalized to a path and hash; I had to notice the size mismatch and raise the limit to 20,000. The rest of the history remains gap-compressed and successful tool results are not included, so confirming completion still required separate reads of the job state, implementation report, and Git state.

## Why Nyxloom owns it

The gap is in Nyxloom's session extraction and successor-handoff surface. A replacement agent needs the exact task contract and later operator instructions together, while progress summaries and claims remain explicitly distinct from evidence.

## Proposed contract

Extend successor briefs with a dedicated task-context section that carries the original assignment and subsequent operator messages separately from sampled progress. Preserve those source messages verbatim and in timestamp order so a gap marker in the general history cannot hide an amendment. Set the default brief budget to include ordinary multi-part assignments of at least 12,000 characters, so the 7,218-character reproduction is inline without a guessed override. If an explicit or total-output budget still externalizes a brief, retain a conspicuous source locator and SHA-256, and say that the next agent must retrieve it before acting. Keep `--order` as the successor's requested operation, not part of the source assignment.

Keep the extract mechanical: it may show implementation claims and evidence pointers, but must not present a claim as verified when successful tool results were omitted.

Provide a compact assignment-audit view alongside the full successor brief for the narrower task of recovering scope and remaining work. It should retain the original assignment and every later operator directive verbatim, separate the successor's `--order`, include the stop state and concise file/effect summaries, and omit routine reads and intermediate progress prose. Mark omitted successful tool results as unavailable evidence so the compact view does not imply that reported gates or changes were verified.

## Oracles

- A fixture with a 7,218-character original task is fully present in a default successor brief without a per-run limit override.
- An operator amendment placed inside a gap in the ordinary history still appears verbatim and timestamped in the dedicated task-context section.
- A deliberately small `--brief-max-chars` produces an explicit externalization notice and the original brief's path and hash; it never silently drops the task.
- `--order` appears as the successor's instruction and is not reported as part of the original assignment or operator amendments.
- A fixture with many routine reads/progress events and multiple later operator directives yields a compact assignment-audit view that preserves every task directive in order, separates `--order`, and omits routine events.
- The handoff marks omitted successful tool results as unavailable evidence, and does not convert assistant completion claims into verified outcomes.

## SPEC ownership

`src/nyxloom/session_extract/README.md` and `docs/CLI-REFERENCE.md`.

## Updates

**2026-10-14** — Direct use on 2026-10-14: nyxloom-harness extract with successor-brief, brief-max-chars 10000, and an explicit order returned the complete 7,218-character original assignment, later OPERATOR directives, separate successor order, file/effect ledger, and stop state in one invocation. The output was 50,994 bytes / 6,984 words from an 8,586,438-byte transcript. That made scope recovery quick without opening the raw log. For a narrow assignment audit, the full successor handoff is more than needed; verifying completion still required checking the live branch/status and implementation report because successful tool results are omitted. Add a compact assignment-audit mode that preserves the original task and every later operator directive verbatim, separates the successor order, includes stop state and touched-file/external-effect summaries, and omits routine reads and progress prose. It must label omitted successful command results as unavailable evidence.
