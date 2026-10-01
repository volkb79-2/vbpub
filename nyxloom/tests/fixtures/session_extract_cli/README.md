# Session CLI acceptance fixtures

These small JSONL files exercise the public CLI module against minimized
Claude Code and Codex record shapes observed in local sessions. The question,
answer, tool, and project text is rewritten test content; the original session
files are private and are never read by the test suite. The fixtures preserve
the relevant record ordering and links while using rewritten content.

| Fixture | Q&A cases | Other session events |
|---|---|---|
| `claude_interview_compaction.jsonl` | Rejected batch with free text and an explicit no-answer row; direct choice; unanswered prompt; displayed headers, option descriptions, and multi-select | Tool intent, hidden command payload, steered compaction |
| `codex_interview_compaction.jsonl` | Prompt with assistant-prose copy; linked choice and free-text answers after intervening activity and compaction; unanswered prompt without prose copy | Tool intent, hidden command payload, deliberate compaction |

The CLI acceptance tests compare the complete normalized JSON event stream to
checked-in goldens. Only the source metadata field is omitted because it
contains an absolute checkout path. An OpenCode database was not available in
the standard local location during this review, so OpenCode remains covered by
its adapter and CLI fixture tests until a representative local source can be
selected.
