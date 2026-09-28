# Session CLI acceptance fixtures

These small JSONL files exercise the public CLI module against minimized
Claude Code and Codex record shapes observed in local sessions. The question,
answer, tool, and project text is rewritten test content; the original session
files are private and are never read by the test suite. The fixtures preserve
the relevant record ordering and links, including a rejected question reply,
choice and free-text answers, an unanswered prompt, tool intent, and
compaction records.

The CLI acceptance tests compare the complete normalized JSON event stream to
checked-in goldens. Only the source metadata field is omitted because it
contains an absolute checkout path. An OpenCode database was not available in
the standard local location during this review, so OpenCode remains covered by
its adapter and CLI fixture tests until a representative local source can be
selected.
