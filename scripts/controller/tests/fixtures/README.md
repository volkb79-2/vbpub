# Controller stream fixtures

These small JSONL streams retain only the event envelope and closing items
needed by the controller checks. Report paths and unrelated conversation
content are redacted where needed.

| Fixture | Source run | Retained behavior |
| --- | --- | --- |
| `reasoning-end.jsonl` | `review-iobench-fill` | A completed turn whose last item is reasoning. |
| `intent-only.jsonl` | `review-rg-flake` | A completed turn ending with an announcement of future work. |
| `usage-limit.jsonl` | `ibx-study` | A thread id and `turn.failed` usage-limit message with reset time. |
| `usage-limit-dated.jsonl` | Codex dated retry observation | A `turn.failed` usage-limit message with a month-name date, ordinal day and 12-hour retry time. |
| `usage-limit-dated-future.jsonl` | Future-date regression fixture | A dated retry far enough in the future to verify recovery scheduling refuses to start it early. |
| `clean-run.jsonl` | `review-iobench-fill-fin` | A completed reviewer message with no stall-class ending. |
| `reviewer-wait-mention.jsonl` | Reviewer close from the stall investigation | A closing message quoting a wait-status phrase without ending on a pending wait. |
| `killed-run.jsonl` | Trimmed interrupted Codex stream | A started turn and final message without `turn.completed`. |
| `stalled-run.jsonl` | `assay-wp3` | The earlier prototype MID-WAIT fixture; its message body is replaced in `test-job-check.sh`. |

The fixtures are intentionally minimal: classification uses the real event
types and messages that distinguish each terminal state, while the tests
construct git, report, and gate-evidence state independently.
