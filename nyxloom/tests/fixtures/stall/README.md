# Stall-check fixtures

These are small, sanitized slices of real Codex runs from
`/home/vscode/vbpub-release-program-20261027/codex/runs/` on 2026-11-10:

- `review-rg-flake.exec.jsonl`: the intent-only ending at the end of the
  original `codex exec --json` stream.
- `session-audit-6.exec.jsonl`: the intent-only ending captured before the
  report was filled; `session-audit-6.skeleton.md` is the corresponding
  pending-report shape.
- `hc4.exec.jsonl`: intent-only "moving into hand-mutation" ending.
- `clean.exec.jsonl`: the completed final report message from `hc4-fin`.
- `interrupted.exec.jsonl`: the terminal usage-limit failure from `nyx-stall`.
- `review-rg-flake.rollout.jsonl`: a trimmed slice of the `~/.codex/sessions`
  rollout whose thread id matches the exec stream.

`ended-mid-wait.exec.jsonl`, `tool-call-as-text.exec.jsonl`, and
`empty.exec.jsonl` preserve real item/record shapes but isolate structural
branches that did not occur together in one selected run. Absolute paths,
thread ids, and command arguments with no classifier value were shortened.
