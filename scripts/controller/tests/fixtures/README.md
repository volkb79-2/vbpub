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
| `pi-success.jsonl` | Sanitized pi 1.5.0 event stream | A completed assistant message and session id. |
| `pi-402.jsonl` | Sanitized pi 1.5.0 error events | `message_end` with `stopReason=error` and a 402 `errorMessage`; pi's process can still exit zero. |
| `pi-402-unknown.jsonl` | Sanitized pi 1.5.0 error events | A 402 `errorMessage` with no `reset_at`, for the bounded unknown-reset rule. |
| `pi-error-other.jsonl` | Sanitized pi 1.5.0 error events | A final `stopReason=error` whose `errorMessage` is not a 402; a plain failure that must not block an account. |
| `pi-402-other-code.jsonl` | Sanitized pi 1.5.0 error events | A 402 `errorMessage` whose JSON `code` is not `insufficient_balance`; a plain failure, not a quota block. |
| `pi-length.jsonl` | Sanitized pi 1.5.0 event stream | A settled run whose final assistant `stopReason` is `length`; not a success. |
| `pi-empty-completion.jsonl` | Sanitized pi 1.5.0 event stream (a store-crate stall) | A settled run whose final assistant message is `stopReason=stop`, `content: []`, 173 output tokens at 211k context; the provider anomaly that ended the task mid-edit. |
| `pi-thinking-only.jsonl` | Sanitized pi 1.5.0 event stream (a store-crate stall) | The same class with a `thinking` block instead of an empty content list, at 177k context. |
| `pi-tool-in-flight.jsonl` | Sanitized pi 1.5.0 event stream (a hung tool call) | A `tool_execution_start` with no matching end: the liveness snapshot must name the tool and when it started. |
| `pi-empty-usage.jsonl` | Sanitized pi 1.5.0 event stream (a store-crate stall) | One turn carrying `usage` on both `message_update` and `message_end`, so a snapshot can read tokens during the turn and after it. |
| `pi-usage-only.jsonl` | Truncated tail of the same stall | `usage` on `message_update` and nothing after it: the only token figure available mid-turn, before any `message_end`. |
| `pi-delta-usage-zero.jsonl` | Measured shape of pi 1.5.0 streaming usage | Every streaming delta (`text_start`, `text_delta`) reports `totalTokens: 0` and the closing `text_end`/`message_end` carries the real total (1771). Measured on captured pi 1.5.0 session streams on this host (2026-11-14/15): with a tool call, `toolcall_start`/`toolcall_delta` also report 0 and `toolcall_end` reports the real total; an `message_start` for a new assistant message reports 0; and a `message_end` for an error turn (`stopReason=error`) can report 0 as well. So 0 is a placeholder on a delta and also a legitimate value on an error turn, which is why the reader keeps the last non-zero measurement and the last report as two facts. |
| `pi-long-run-zero-reports.jsonl` | Synthesized from that measured shape | A real completion at `totalTokens: 258492`, then a turn whose deltas all report 0, with a tool in flight: the snapshot must publish 258492 and keep the zero report visible as its own fact. |
| `pi-whitespace-only.jsonl` | Synthesized from the empty-completion class | A settled `stop` whose assistant message carries text that is only whitespace: "has text" must mean text after stripping, or a blank message would read as a completion. |
| `pi-tool-progress.jsonl` | Measured shape of a working tool | `tool_execution_start` followed by `tool_execution_update` carrying `partialResult`: the tool's own partial output is progress evidence, so a tool that keeps printing never reads as hung even when the stream is otherwise quiet. |

The fixtures are intentionally minimal: classification uses the real event
types and messages that distinguish each terminal state, while the tests
construct git, report, and gate-evidence state independently.
