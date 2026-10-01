# P113 report: session extraction CLI acceptance and Claude question parsing

Date: 2026-10-01
Result: **DONE** — acceptance coverage, review, and the full current-main
session-extract R2 campaign passed. The feature is integrated into main.

## Summary

P113 adds sanitized Claude Code and Codex session fixtures with complete
normalized JSON and rendered-text goldens. Subprocess acceptance tests invoke
the public extract CLI and cover question and answer timing, answer forms,
rejected and unanswered questions, assistant prose copies, compaction, and
tool descriptions while excluding command payloads.

Claude Code question output now retains the displayed header, option labels
and descriptions, and multi-select behavior. Recognized answers repeat that
context with OPERATOR. Rejected tool-result envelopes are interpreted only
when every row matches the original request; malformed or ambiguous material
remains raw source context. D-019 records the output contract.

The final R2 survivor led to one additional direct contract assertion:
malformed rejected-question JSON returns None from its decoder. The output
consumer treats None and an empty list equivalently, so the existing
output-level fallback test alone could not assert the decoder's annotated
str-or-None contract.

## Oracle and evidence summary

| Check | Result | Evidence |
|---|---|---|
| Sanitized CLI acceptance | PASS | Claude Code and Codex fixture transcripts are compared against complete JSON and prose goldens by subprocess tests. |
| Declared tester-unified gate on the integrated feature | PASS | Commit c1d0fbe2: R0 PASS; R1 PASS at 655/655 changed executable lines and 166/166 branches. |
| Gate after malformed-input fixes | PASS | Commit cc818af1: R0 PASS; R1 PASS at 15/15 changed executable lines and 2/2 branches. |
| Final session-extract coverage | PASS | Commit 8df26ed1: R0 PASS; R1 PASS at 117/117 changed lines and 60/60 branches; R3 PASS. |
| Final current-main R2 | PASS | All 43 of 43 candidates killed; zero survivors, equivalents, crashes, hangs, or budget overruns. |
| Post-coverage review | PASS | Reviewed the Claude parser boundaries, malformed input behavior, direct decoder assertion, and final diff. No remaining findings. |

## Final R2 campaign

The accepted registered lane invocation was:

    ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-r2-decoder-fix session-extract

It judged commit 8df26ed143925c882e65a1fe673d1447055bd754 and resolved base
126ccc39e151e33cc7bbcaa18bf765f9c9cd7dd1 by merge-base. The plan selected
43 candidates under the approved cap of 45: 1 boolean-constant, 13
boolean-operator, 17 comparison, and 12 falsy-swap candidates. R2 used
jobs=2. The run started at 2026-10-01T02:18:37Z and ended at
2026-10-01T02:31:18Z; run-gate recorded 771.803 seconds.

The final verdict is
evidence/session-extract-8df26ed1-final-verdict.json. Its SHA256 is
9c6e5e856f87cb7ae828ee90644ab2e211f8fef268ba5a62a2154c8c1814ea65.
The progress stream is
evidence/session-extract-8df26ed1-final-progress.jsonl; SHA256
8520c81991ec95c223bda7f789f934f44f4fedf345c36262efc2c2cfc8168cab.
The captured run-gate history is
evidence/run-gate-history-8df26ed1.json; SHA256
a9e52bbb047d7e552671aa2ff700ab6955632cfc1b5dc2795f3ed9ebbd4b16fd.

## Scope correction and prior attempts

The first combined R2 on merge commit 2ba90c10 selected the intended 45
candidates using first-parent scope, but 8 survived. Seven identified missing
behavior assertions in Claude question parsing and formatting; the eighth
boundary comparison was redundant with a following guard. Fix cc818af1 added
malformed, empty, and out-of-order envelope cases; formatting cases; and
non-object tool-input cases. The new tests exposed and fixed a real .get()
failure for a non-object input. Two redundant boundary checks were consolidated.

A subsequent run at merge commit 6ee297a4 reported PASS but selected only one
candidate because Assay resolved the first parent 2ba90c10 as its base. It was
a narrow check, not the approved campaign. A single-parent follow-up restored
the intended merge-base boundary. The accepted final run then measured all 43
candidates against current main. The earlier outcomes and the scope correction
are preserved in the execution log and evidence directory.

## Files changed

- Claude parser and tests: session_extract/adapters/claude_code.py,
  test_session_extract_claude_code.py, and test_session_extract_follow.py.
- Acceptance corpus: tests/fixtures/session_extract_cli/ including sanitized
  Claude Code and Codex JSONL, expected JSON, expected text, and its README;
  test_session_extract_cli_acceptance.py.
- User documentation: nyxloom README, DESIGN-GUIDE, CONSUMERS, CLI reference,
  USAGE, and the session-extraction package README.
- Closeout evidence: nyxloom-P113-LOG.md and the evidence artifacts listed
  above.

## Disposition

P113 is complete and integrated. The final campaign used the approved
45-candidate boundary with two jobs and finished in about 13 minutes.
Historical failed and scope-limited runs remain explicitly identified rather
than being presented as final evidence.
