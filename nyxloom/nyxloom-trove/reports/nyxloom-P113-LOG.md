# nyxloom-P113 execution log

## Scope — 2026-09-28

The user asked whether selected Claude Code and Codex sessions should become
session-extraction acceptance data, with coverage for question/answer timing,
answer forms, compaction, and tool descriptions. The user selected Claude Code
and Codex as the first source formats; OpenCode can be added later.

Work is isolated in
`/workspaces/vbpub/.worktrees/nyxloom-session-cli-acceptance`, branch
`nyxloom-session-cli-acceptance`. The feature branch is based on provisional
main commit `a1f88cb5247683a8b19b82436f84fe1313697826`.

## Acceptance data and implementation

- Added small sanitized Claude Code and Codex JSONL fixtures, full normalized
  JSON goldens, and full text-render goldens under
  `tests/fixtures/session_extract_cli/`. The tests do not read the real local
  session files; source text and workspace paths in fixtures are rewritten.
- Added subprocess tests that invoke `python -m nyxloom.cli_harness extract`
  against each fixture and compare the complete normalized event stream and
  rendered prose. The cases cover an immediate choice, later choice and free
  text replies, a Claude rejected question batch with an explicit no-answer
  row, an unanswered prompt, assistant prose copies, a steered compaction,
  and tool calls with descriptions while excluding command payloads.
- Claude Code question prose now includes the displayed header, option labels
  and descriptions, and multi-select behavior at the tool-call record. A
  recognized answer repeats that context with `OPERATOR:`. Rejected tool-result
  envelopes are parsed only when all rows match the original request; otherwise
  the source text is preserved. D-019 records this output contract.
- Updated the Nyxloom README, DESIGN-GUIDE, CONSUMERS guide, and the
  session-extraction package guide.

## Private source smoke runs

Ran the same source-checkout CLI against three user-selected local session
files with `--profile all --json --show-tool-calls --show-tool-call-intent
--show-compaction-content`. No source bodies or raw extracts were copied into
the repository.

| Format | Source question items | Prompt items emitted | Unmatched | `OPERATOR:` blocks | Tool calls | Lifecycle events | CLI |
|---|---:|---:|---:|---:|---:|---:|---:|
| Codex | 15 | 15 | 0 | 4 | 545 | 6 | PASS |
| Claude Code | 18 | 18 | 0 | 18 | 881 | 3 | PASS |
| Claude Code | 7 | 7 | 0 | 6 | 568 | 5 | PASS |

The last Claude file has one `AskUserQuestion` tool result denied by a hook;
it stays raw tool-result context and is not counted as an operator answer.
These counts distinguish question tool-call records from confirmed UI display.
An OpenCode database was not present in its supported local lookup location,
so no OpenCode real-source run was available for this first corpus.

## Test evidence

The targeted files passed after the adapter changes:

```text
/home/vscode/.venv/bin/python -m pytest -q tests/test_session_extract_follow.py tests/test_session_extract_claude_code.py tests/test_session_extract_cli_acceptance.py
exit: 0
```

The complete session-extraction test set also passed:

```text
/home/vscode/.venv/bin/python -m pytest -q tests/test_session_extract_*.py
exit: 0
```

`git diff --check` passed. This is devcontainer diagnostic evidence, not the
authoritative tester-unified gate.

## Pending authoritative evidence

The user's requested session-extract R2 campaign is running separately on the
`nyxloom-cli-adoption` worktree. No heavy gate was started in parallel. The
current P113 revision still needs a clean commit and its declared
tester-unified gate after the active R2 run finishes and any resulting fixes
are reviewed. Record the exact gate output and final commit in
`nyxloom-P113-REPORT.md` before calling this work complete.
