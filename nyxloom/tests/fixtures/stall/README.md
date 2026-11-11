# Stall-check fixtures

These are small, sanitized slices copied from completed controller Codex runs.
They preserve the source record shapes and relevant final prose while removing
developer-local paths and incidental command details:

- `review-rg-flake.exec.jsonl`: the intent-only ending at the end of the
  original `codex exec --json` stream.
- `session-audit-6.exec.jsonl`: the intent-only ending captured before the
  report was filled; `session-audit-6.skeleton.md` is the corresponding
  pending-report shape.
- `hc4.exec.jsonl`: intent-only "moving into hand-mutation" ending.
- `nyx-stall2-replacing.exec.jsonl`: the observed present-continuous ending
  where the agent was replacing its report.
- `no-comma-active-wait.exec.jsonl`: the independent review's paired B5
  boundary case: a discourse-opening `No,` followed by an active wait.
- `clean.exec.jsonl`: the completed final report message from `hc4-fin`.
- `interrupted.exec.jsonl`: the terminal usage-limit failure from `nyx-stall`.
- `usage-limit-no-message.exec.jsonl`: a terminal usage-limit failure with no
  assistant message.
- `review-rg-flake.rollout.jsonl`: a trimmed slice of the durable rollout whose
  thread id matches the exec stream.

`ended-mid-wait.exec.jsonl`, `tool-call-as-text.exec.jsonl`, and
`empty.exec.jsonl` preserve real item/record shapes but isolate structural
branches that did not occur together in one selected run. `tool-call-as-text`
is explicitly synthetic. Repository-relative report labels, thread ids, and
command arguments with no classifier value were shortened.
